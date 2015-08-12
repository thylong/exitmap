# Copyright 2013-2015 Philipp Winter <phw@nymity.ch>
#
# This file is part of exitmap.
#
# exitmap is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# exitmap is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with exitmap.  If not, see <http://www.gnu.org/licenses/>.

"""
Handles Tor controller events.
"""

import sys
import functools
import threading
import multiprocessing
import socket

import stem
from stem import StreamStatus
from stem import CircStatus

import command
import util
import log

logger = log.get_logger()


class Attacher(object):

    """
    Attaches streams to circuits.
    """

    def __init__(self, controller):

        # Maps port to function that attached a stream to a circuit.

        self.unattached = {}
        self.controller = controller

    def prepare(self, port, circuit_id=None, stream_id=None):
        """
        Prepare for attaching a stream to a circuit.

        If we already have the corresponding stream/circuit, we can attach it
        now.  Otherwise, the method _attach() is partially executed and stored,
        so it can be attached later.
        """

        assert ((circuit_id is not None) and (stream_id is None)) or \
               ((circuit_id is None) and (stream_id is not None))

        # Check if we can already attach.

        if port in self.unattached:
            attach = self.unattached[port]

            if circuit_id:
                attach(circuit_id=circuit_id)
            else:
                attach(stream_id=stream_id)

            del self.unattached[port]
        else:
            # We maintain a dictionary of source ports that point to their
            # respective attaching function.  At this point we only know either
            # the stream or the circuit ID, so we store a partially executed
            # function.

            if circuit_id:
                partially_attached = functools.partial(self._attach,
                                                       circuit_id=circuit_id)
                self.unattached[port] = partially_attached
            else:
                partially_attached = functools.partial(self._attach,
                                                       stream_id=stream_id)
                self.unattached[port] = partially_attached

        logger.debug("Pending attachers: %d." % len(self.unattached))

    def _attach(self, stream_id=None, circuit_id=None):
        """
        Attach a stream to a circuit.
        """

        logger.debug("Attempting to attach stream %s to circuit %s." %
                     (stream_id, circuit_id))

        try:
            self.controller.attach_stream(stream_id, circuit_id)
        except stem.OperationFailed as err:
            logger.warning("Failed to attach stream because: %s" % err)


def decorator(queue, module, circ_id, *module_args):

    def wrapper():

        try:
            module(*module_args)

            logger.debug("Informing event handler that module finished.")
            queue.put((circ_id, None))
        except KeyboardInterrupt:
            pass

    return wrapper


class EventHandler(object):

    """
    Handles asynchronous Tor events.

    The handler processes only stream and circuit events.  New streams are
    attached to their corresponding circuits since exitmap's Tor process leaves
    new streams unattached.
    """

    def __init__(self, controller, module, socks_port, stats):

        self.stats = stats
        self.controller = controller
        self.attacher = Attacher(controller)
        self.module = module
        self.manager = multiprocessing.Manager()
        self.queue = self.manager.Queue()
        self.socks_port = socks_port

        queue_thread = threading.Thread(target=self.queue_reader)
        queue_thread.daemon = False
        queue_thread.start()

    def queue_reader(self):
        """
        Read (circuit ID, sockname) tuples from invoked probing modules.

        These tuples are then used to attach streams to their corresponding
        circuits.
        """

        logger.debug("Starting thread to read from IPC queue.")

        while True:
            try:
                circ_id, sockname = self.queue.get()
            except EOFError:
                logger.debug("IPC queue terminated.")
                break

            # Over the queue, a module can either signal that it finished
            # execution (by sending (circ_id,None)) or that it is ready to have
            # its stream attached to a circuit (by sending (circ_id,sockname)).

            if sockname is None:
                logger.debug("Closing finished circuit %s." % circ_id)
                try:
                    self.controller.close_circuit(circ_id)
                except stem.InvalidArguments as err:
                    logger.debug("Could not close circuit because: %s" % err)

                self.stats.finished_streams += 1
                self.stats.print_progress()
                self.check_finished()
            else:
                _, port = sockname[0], int(sockname[1])
                logger.debug("Read from queue: %s, %s" % (circ_id,
                                                          str(sockname)))
                self.attacher.prepare(port, circuit_id=circ_id)
                self.check_finished()

    def check_finished(self):
        """
        Check if the scan is finished and if it is, shut down exitmap.
        """

        # Did all circuits either build or fail?

        circs_done = ((self.stats.failed_circuits +
                       self.stats.successful_circuits) ==
                      self.stats.total_circuits)

        # Was every built circuit attached to a stream?

        streams_done = (self.stats.finished_streams >=
                        (self.stats.successful_circuits -
                         self.stats.failed_circuits))

        logger.debug("failedCircs=%d, builtCircs=%d, totalCircs=%d, "
                     "finishedStreams=%d" % (
                         self.stats.failed_circuits,
                         self.stats.successful_circuits,
                         self.stats.total_circuits,
                         self.stats.finished_streams))

        if circs_done and streams_done:

            for proc in multiprocessing.active_children():
                logger.debug("Terminating remaining PID %d." % proc.pid)
                proc.terminate()

            if hasattr(self.module, "teardown"):
                logger.debug("Calling module's teardown() function.")
                self.module.teardown()

            logger.info(self.stats)
            sys.exit(0)

    def new_circuit(self, circ_event):
        """
        Invoke a new probing module when a new circuit becomes ready.
        """

        self.stats.update_circs(circ_event)
        self.check_finished()

        if circ_event.status not in [CircStatus.BUILT]:
            return

        last_hop = circ_event.path[-1]
        exit_fpr = last_hop[0]
        logger.debug("Circuit for exit relay \"%s\" is built.  "
                     "Now invoking probing module." % exit_fpr)

        torsocks_file = util.create_temp_torsocks_conf(self.socks_port)
        run_cmd_over_tor = command.Command(torsocks_file,
                                           self.queue,
                                           circ_event.id,
                                           socket.socket)

        module = decorator(self.queue, self.module.probe,
                           circ_event.id, exit_fpr,
                           command.run_python_over_tor(self.queue,
                                                       circ_event.id,
                                                       self.socks_port),
                           run_cmd_over_tor)

        proc = multiprocessing.Process(target=module)
        proc.daemon = True
        proc.start()

    def new_stream(self, stream_event):
        """
        Create a function which is later used to attach a stream to a circuit.

        The attaching cannot be done right now as we do not know the stream's
        desired circuit ID at this point.  So we set up all we can at this
        point and wait for the attaching to be done in queue_reader().
        """

        if stream_event.status not in [StreamStatus.NEW,
                                       StreamStatus.NEWRESOLVE]:
            return

        port = util.get_source_port(str(stream_event))
        if not port:
            logger.warning("Couldn't extract source port from stream "
                           "event: %s" % str(stream_event))
            return

        logger.debug("Adding attacher for new stream %s." % stream_event.id)
        self.attacher.prepare(port, stream_id=stream_event.id)
        self.check_finished()

    def new_event(self, event):
        """
        Dispatches new Tor controller events to the appropriate handlers.
        """

        if isinstance(event, stem.response.events.CircuitEvent):
            self.new_circuit(event)

        elif isinstance(event, stem.response.events.StreamEvent):
            self.new_stream(event)

        else:
            logger.warning("Received unexpected event %s." % str(event))
