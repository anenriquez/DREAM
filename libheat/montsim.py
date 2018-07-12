import numpy as np

from . import srea
from . import functiontimer
from . import printers as pr

Z_NODE_ID = 0


class Simulator(object):
    def __init__(self, random_seed=None):
        # Nothing here for now.
        self.stn = None
        self.assignment_stn = None
        self._current_time = 0.0

        self._rand_seed = random_seed
        self._rand_state = np.random.RandomState(random_seed)
        self.num_reschedules = 0
        self.num_sent_schedules = 0

    def simulate(self, starting_stn, execution_strat, sim_options={}):
        ''' Run one simulation.

        Args:
            starting_stn: The STN used to run in the simulation.
            execution_strat: String representing the strategy to use for
                timepoint execution. Acceptable execution strategies include--
                "early",
                "drea",
                "d-drea",
                "drea-si",
                "drea-ar"

        Keyword Args:
            sim_options: A dictionary of possible options to pass into the

        Returns:
            Boolean indicating whether the simulation was successful or not.
        '''
        # Initial setup
        self._current_time = 0.0
        self.stn = starting_stn.copy()
        self.assignment_stn = starting_stn.copy()
        self._ar_contingent_event_counter = 0
        self.num_reschedules = 0
        self.num_sent_schedules = 0
        # Resample the contingent edges.
        # Super important!
        pr.verbose("Resampling Stored STN")
        self.resample_stored_stn()

        # Setup options
        first_run = True
        options = {"first_run": True,
                   "executed_contingent": False}
        if "si_threshold" in sim_options:
            options["si_threshold"] = sim_options["si_threshold"]
        if "ar_threshold" in sim_options:
            options["ar_threshold"] = sim_options["ar_threshold"]
        if "alp_threshold" in sim_options:
            options["alp_threshold"] = sim_options["alp_threshold"]

        # Setup default guide settings
        guide_stn = self.stn
        current_alpha = 0.0

        # Loop until all timepoints assigned.
        while not self.all_assigned():
            options["first_run"] = first_run
            if first_run:
                first_run = False

            # Calculate the guide STN.
            pr.vverbose("Getting Guide...")
            functiontimer.start("get_guide")
            current_alpha, guide_stn = self.get_guide(execution_strat,
                                                      current_alpha,
                                                      guide_stn,
                                                      options=options)
            #print("GUIDE")
            #print(guide_stn)
            functiontimer.stop("get_guide")
            pr.vverbose("Got guide")

            # Select the next timepoint.
            pr.vverbose("Selecting timepoint...")
            functiontimer.start("selection")
            selection = self.select_next_timepoint(guide_stn,
                                                   self._current_time)
            functiontimer.stop("selection")
            pr.vverbose("Selected timepoint, node_id of {}"
                        .format(selection[0]))

            next_vert_id = selection[0]
            next_time = selection[1]
            executed_contingent = selection[2]

            options['executed_contingent'] = executed_contingent

            # Propagate constraints (minimise) and check consistency.
            self.assign_timepoint(guide_stn, next_vert_id, next_time)
            self.assign_timepoint(self.stn, next_vert_id, next_time)
            self.assign_timepoint(self.assignment_stn, next_vert_id, next_time)
            functiontimer.start("propagation & check")
            stn_copy = self.stn.copy()
            consistent = self.propagate_constraints(stn_copy)
            if not consistent:
                pr.verbose("Assignments: " + str(self.get_assigned_times()))
                pr.verbose("Failed to place point {}, at {}"
                           .format(next_vert_id, next_time))
                return False
            self.stn = stn_copy
            pr.vverbose("Done propagating our STN")
            functiontimer.stop("propagation & check")

            # Clean up the STN
            self.remove_old_timepoints(self.stn)

            self._current_time = next_time
        pr.verbose("Assignments: " + str(self.get_assigned_times()))
        pr.verbose("Successful!")
        return True

    def select_next_timepoint(self, dispatch, current_time):
        """ Retrieves the earliest possible vert.
        Ties are broken arbitrarily.

        Args:
            dispatch: STN which is used for getting the right dispatch.
            current_time: Current time of the simulation.

        Returns:
            Returns a tuple of (vert, time) where 'vert' is the vert ID
            of the vert which has the earliest assignment time, and 'time'
            If no timepoint can be selected, returns (None, inf)
        """
        earliest_so_far = None
        earliest_so_far_time = float("inf")
        has_incoming_contingent = False

        #print("Selecting...")
        #print(dispatch)

        # This could be sped up. We only want unexecuted verts without parents.
        for i, vert in dispatch.verts.items():
            # Don't recheck already executed verts
            if vert.is_executed():
                continue
            # Check if all predecessors are executed -> enabled.
            predecessor_ids = [e.i for e in dispatch.get_incoming(i)]
            predecessors = [dispatch.get_vertex(q) for q in predecessor_ids]
            is_enabled = all([p.is_executed() for p in predecessors])
            # Exit early if not enabled.
            if not is_enabled:
                continue
            incoming_contingent = dispatch.get_incoming_contingent(i)
            if incoming_contingent is None:
                # Get the
                # Make sure that we can't go back in time though.
                incoming_reqs = dispatch.get_incoming(i)
                if incoming_reqs == []:
                    earliest_time = 0.0
                else:
                    earliest_time = max([edge.get_weight_min()
                                         + self.stn.get_assigned_time(edge.i)
                                         for edge in incoming_reqs])
            else:
                sample_time = incoming_contingent.sampled_time()
                # Get the contingent edge's predecessor
                cont_pred = incoming_contingent.i
                assigned_time = dispatch.get_assigned_time(cont_pred)
                if assigned_time is None:
                    # This is an incredibly bizarre edge case that SREA
                    # sometimes produces: It alters the assigned points to
                    # an invalid time. One work around is to manually find the
                    # UPPER bound (not the lower bound), because that appears
                    # untouched by SREA.
                    pr.warning("Executed event was not assigned.")
                    pr.warning("Event was: {}".format(cont_pred))
                    vert = dispatch.get_vertex(cont_pred)
                    new_time = dispatch.get_edge_weight(Z_NODE_ID,
                                                        cont_pred)
                    msg = "Re-assigned to: {}".format(new_time)
                    pr.warning(msg)
                    earliest_time = new_time
                else:
                    earliest_time = dispatch.get_assigned_time(cont_pred) \
                        + sample_time
            # Update the earliest time  now.
            if earliest_so_far_time > earliest_time:
                earliest_so_far = i
                earliest_so_far_time = earliest_time
                has_incoming_contingent = (incoming_contingent is not None)
        return (earliest_so_far, earliest_so_far_time,
                has_incoming_contingent)

    def assign_timepoint(self, stn, vert_id, time):
        """ Assigns a timepoint to specified time

        Args:
            vert: Node to assign.
            time: float: Time to assign this vert.
        """
        if vert_id != Z_NODE_ID:
            stn.update_edge(Z_NODE_ID,
                            vert_id,
                            time,
                            create=True,
                            force=True)
            stn.update_edge(vert_id,
                            Z_NODE_ID,
                            -time,
                            create=True,
                            force=True)
        stn.get_vertex(vert_id).execute()

    def propagate_constraints(self, stn_to_prop):
        """ Updates current constraints and minimises
        """
        functiontimer.start("propogate_constraints")
        ans = stn_to_prop.floyd_warshall()
        functiontimer.stop("propogate_constraints")
        return ans

    def all_assigned(self) -> bool:
        """ Check if all vertices of the STN have been assigned
        """
        for vert in self.stn.get_all_verts():
            if not vert.is_executed():
                return False
        return True

    def remove_old_timepoints(self, stn) -> None:
        """ Remove timepoints which add no new information, as they exist
        entirely in the past, and have no lingering constraints that are not
        already captured.
        """
        stored_keys = list(stn.verts.keys())
        for v_id in stored_keys:
            if v_id == 0:
                continue
            if (stn.outgoing_executed(v_id) and
                    stn.get_vertex(v_id).is_executed()):
                stn.remove_vertex(v_id)

    def resample_stored_stn(self) -> None:
        for e in self.stn.contingent_edges.values():
            e.resample(self._rand_state)

    def get_assigned_times(self) -> dict:
        times = {}
        for key, v in self.assignment_stn.verts.items():
            if v.is_executed():
                times[key] = (self.assignment_stn.get_assigned_time(key))
            else:
                times[key] = (None)
        return times

    def get_guide(self, execution_strat, previous_alpha,
                  previous_guide, options={}) -> tuple:
        """ Retrieve a guide STN (dispatch) based on the execution strategy
        Args:
            execution_strat: String representing the execution strategy.
            previous_: The previously used guide STN's alpha.
            previous_guide: The previously used guide STN.

        Keyword Args:
            options: Dictionary of possible options to use for the algorithms.

        Return:
            Returns a tuple with format:
            [0]: Alpha of the guide.
            [1]: dispatch (type STN) which the simulator should follow,
        """
        if execution_strat == "early":
            return 1.0, self.stn
        elif execution_strat == "srea":
            return self._srea_algorithm(previous_alpha,
                                        previous_guide,
                                        options["first_run"])
        elif execution_strat == "drea":
            return self._drea_algorithm(previous_alpha,
                                        previous_guide,
                                        options["first_run"],
                                        options["executed_contingent"])
        elif execution_strat == "drea-si":
            return self._drea_si_algorithm(previous_alpha,
                                           previous_guide,
                                           options["first_run"],
                                           options["executed_contingent"],
                                           options["si_threshold"])
        elif execution_strat == "drea-alp":
            return self._drea_alp_algorithm(previous_alpha,
                                            previous_guide,
                                            options["first_run"],
                                            options["executed_contingent"],
                                            options["alp_threshold"])
        elif execution_strat == "drea-ar":
            if options["executed_contingent"]:
                self._ar_contingent_event_counter += 1
            ans = self._drea_ar_algorithm(previous_alpha,
                                          previous_guide,
                                          options["first_run"],
                                          options["executed_contingent"],
                                          options["ar_threshold"],
                                          self._ar_contingent_event_counter)
            self._ar_contingent_event_counter = ans[2]
            return ans[0], ans[1]
        elif execution_strat == "arsi":
            if options["executed_contingent"]:
                self._ar_contingent_event_counter += 1
            ans = self._arsi_algorithm(previous_alpha,
                                       previous_guide,
                                       options["first_run"],
                                       options["executed_contingent"],
                                       self._ar_contingent_event_counter,
                                       ar_threshold=options["ar_threshold"],
                                       si_threshold=options["si_threshold"])
            self._ar_contingent_event_counter = ans[2]
            return ans[0], ans[1]
        else:
            raise ValueError(("Execution strategy '{}'"
                              " unknown").format(execution_strat))

    def _srea_wrapper(self, previous_alpha, previous_guide):
        """ Small wrapper to run SREA or keep the same guide if it's not
            consistent.
        """
        result = srea.srea(self.stn)
        if result is not None:
            return result[0], result[1]
        # Our guide was inconsistent... um. Well.
        # This is not great.
        # Follow the previous guide?
        return previous_alpha, previous_guide

    def _srea_algorithm(self, previous_alpha, previous_guide, first_run):
        """ Implements the SREA algorithm. """
        if first_run:
            self.num_reschedules += 1
            self.num_sent_schedules += 1
            return self._srea_wrapper(previous_alpha, previous_guide)
        # Not our first run, use the previous guide.
        return previous_alpha, previous_guide

    def _drea_algorithm(self, previous_alpha, previous_guide, first_run,
                        executed_contingent):
        """ Implements the DREA algorithm. """
        if first_run or executed_contingent:
            self.num_reschedules += 1
            self.num_sent_schedules += 1
            ans = self._srea_wrapper(previous_alpha, previous_guide)
            pr.verbose("DREA Rescheduled, new alpha: {}".format(ans[0]))
            return ans
        return previous_alpha, previous_guide

    def _drea_si_algorithm(self, previous_alpha, previous_guide, first_run,
                           executed_contingent, threshold):
        """ Implements the DREA-SI algorithm. """
        # Exit early if the STN was not consistent at all.

        if first_run:
            result = srea.srea(self.stn)
            self.num_reschedules += 1
            self.num_sent_schedules += 1
            if result is None:
                return previous_alpha, previous_guide
            new_alpha = result[0]
            maybe_guide = result[1]
            pr.verbose("Got new drea-si guide with alpha={}".format(new_alpha))
            return new_alpha, maybe_guide
        # We should only run this algorithm *if* we recently executed
        # a receieved/contingent timepoint.
        if not executed_contingent:
            return previous_alpha, previous_guide
        # Reschedule
        result = srea.srea(self.stn)
        self.num_reschedules += 1
        if result is None:
            return previous_alpha, previous_guide
        new_alpha = result[0]
        maybe_guide = result[1]

        # num_cont : Number of remaining unexecuted contingent events
        num_cont = self.remaining_contingent_count(maybe_guide)
        p_0 = (1-previous_alpha)**num_cont
        p_1 = (1-new_alpha)**num_cont
        if p_1 - p_0 > threshold:
            self.num_sent_schedules += 1
            pr.verbose("Got new drea-si guide with alpha={}".format(new_alpha))
            return new_alpha, maybe_guide
        else:
            pr.verbose("Did not reschedule, p_0={}, p_1={}".format(p_0, p_1))
            return previous_alpha, previous_guide

    def _drea_alp_algorithm(self, previous_alpha, previous_guide, first_run,
                            executed_contingent, threshold):
        """ Implements the DREA alpha difference algorithm, which is an attempt
        to correct DREA-SI which has a fatal flaw of not rescheduling when
        contingent events tend to differ.
        """
        if first_run:
            self.num_reschedules += 1
            result = srea.srea(self.stn)
            if result is None:
                return previous_alpha, previous_guide
            new_alpha = result[0]
            maybe_guide = result[1]
            pr.verbose("Got new drea-si guide with alpha={}".format(new_alpha))
            return new_alpha, maybe_guide
        # We should only run this algorithm *if* we recently executed
        # a receieved/contingent timepoint.
        if not executed_contingent:
            return previous_alpha, previous_guide
        # We are therefore actually running the algorithm.
        result = srea.srea(self.stn)
        self.num_reschedules += 1
        if result is None:
            return previous_alpha, previous_guide
        new_alpha = result[0]
        maybe_guide = result[1]
        # num_cont : Number of remaining unexecuted contingent events
        num_cont = 0
        for i in maybe_guide.received_timepoints:
            if not maybe_guide.get_vertex(i).is_executed():
                num_cont += 1

        if abs(new_alpha - previous_alpha) > threshold:
            pr.verbose("Got new drea-si guide with alpha={}".format(new_alpha))
            self.num_sent_schedules += 1
            return new_alpha, maybe_guide
        else:
            pr.verbose("Did not send reschedule, a0={}, a1={}"
                       .format(previous_alpha, new_alpha))
            return previous_alpha, previous_guide

    def _drea_ar_algorithm(self, previous_alpha, previous_guide, first_run,
                           executed_contingent, threshold,
                           contingent_event_counter):
        """ Implements the DREA-AR algorithm. """
        if first_run:
            result = srea.srea(self.stn)
            if result is not None:
                self.num_reschedules += 1
                return result[0], result[1], contingent_event_counter

        # We should only run this algorithm *if* we recently executed
        # a receieved/contingent timepoint.
        if not executed_contingent:
            return previous_alpha, previous_guide, contingent_event_counter

        # n is a placeholder for how much uncertainty we can take.
        n = 0
        attempts = 0  # Make sure we can actually escape if threshold = 0
        while (1-previous_alpha)**(n+1) > threshold and attempts < 100:
            n += 1
            attempts += 1

        # Temporary variable to maintain unique names.
        new_counter = contingent_event_counter
        if contingent_event_counter >= n or first_run:
            result = srea.srea(self.stn)
            if result is not None:
                pr.verbose("DREA-AR rescheduled our STN")
                new_alpha = result[0]
                maybe_guide = result[1]
                new_counter = 0
                self.num_reschedules += 1
                self.num_sent_schedules += 1
                return new_alpha, maybe_guide, new_counter
        return previous_alpha, previous_guide, new_counter

    def _arsi_algorithm(self, previous_alpha, previous_guide, first_run,
                        executed_contingent, contingent_event_counter,
                        ar_threshold=0.5,
                        si_threshold=0.5):
        """Implements the ARSI algorithm."""
        if first_run:
            result = srea.srea(self.stn)
            if result is not None:
                self.num_reschedules += 1
                new_alpha = result[0]
                new_guide = result[1]
                return new_alpha, new_guide, contingent_event_counter
            return previous_alpha, previous_guide, contingent_event_counter
        # We should only run this algorithm *if* we recently executed
        # a receieved/contingent timepoint.
        if not executed_contingent:
            return previous_alpha, previous_guide, contingent_event_counter
        # AR SECTION ----------------------------------------------------------
        # n is a placeholder for how much uncertainty we can take.
        n = 0
        attempts = 0  # Make sure we can actually escape if threshold = 0
        while (1-previous_alpha)**(n+1) > ar_threshold and attempts < 100:
            n += 1
            attempts += 1
        # Should we reschedule?
        result = None
        if contingent_event_counter >= n:
            # Get a new schedule
            pr.verbose("ARSI rescheduled...")
            result = srea.srea(self.stn)
            self.num_reschedules += 1
        if result is None:
            # Early exit if SREA failed OR if it's not time yet to reschedule
            return previous_alpha, previous_guide, contingent_event_counter
        # SI SECTION ----------------------------------------------------------
        new_alpha = result[0]
        maybe_guide = result[1]

        num_cont = self.remaining_contingent_count(maybe_guide)
        p_0 = (1-previous_alpha)**num_cont
        p_1 = (1-new_alpha)**num_cont
        if p_1 - p_0 > si_threshold:
            self.num_sent_schedules += 1
            pr.verbose("Got new ARSI guide with alpha={}".format(new_alpha))
            return new_alpha, maybe_guide, 0
        else:
            pr.verbose("ARSI did not send schedule, p_0={}, p_1={}"
                       .format(p_0, p_1))
            return previous_alpha, previous_guide, contingent_event_counter
        return previous_alpha, previous_guide, contingent_event_counter

    def remaining_contingent_count(self, stn):
        """Returns the number of remaining (unexecuted) contingent events"""
        # num_cont : Number of remaining unexecuted contingent events
        num_cont = 0
        for i in stn.received_timepoints:
            if not stn.get_vertex(i).is_executed():
                num_cont += 1
        return num_cont