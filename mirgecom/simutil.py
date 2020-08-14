__copyright__ = """
Copyright (C) 2020 University of Illinois Board of Trustees
"""

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""
import numpy as np
from meshmode.dof_array import thaw
from mirgecom.io import (
    make_io_fields,
    make_status_message,
    make_output_dump,
)
from mirgecom.checkstate import compare_states
from mirgecom.euler import (
    get_inviscid_timestep,
)

r"""
This module provides some convenient utilities for
building simulation applications.
"""


def check_step(step, interval):
    """
    Utility to check step number against a user-specified interval. Useful for
    checking whether the current step is an output step, or anyting else that
    occurs on fixed intervals.
    """
    if interval == 0:
        return True
    elif interval < 0:
        return False
    elif step % interval == 0:
        return True
    return False


def inviscid_sim_timestep(discr, state, t, dt, cfl, eos,
                          t_final, constant_cfl=False):
    """
    Wrapper function returns the dt for the next step.
    """
    mydt = dt
    if constant_cfl is True:
        mydt = get_inviscid_timestep(discr=discr, q=state,
                                     cfl=cfl, eos=eos)
    if (t + mydt) > t_final:
        mydt = t_final - t
    return mydt


def sim_checkpoint(discr, visualizer, eos, logger, q, vizname, exact_soln=None,
                   step=0, t=0, dt=0, cfl=1.0, nstatus=-1, nviz=-1, exittol=1e-16,
                   constant_cfl=False, comm=None):
    r"""
    Checkpointing utility for runs with known exact solution generator
    """

    do_viz = check_step(step=step, interval=nviz)
    do_status = check_step(step=step, interval=nstatus)
    if do_viz is False and do_status is False:
        return 0

    actx = q[0].array_context
    nodes = thaw(actx, discr.nodes())
    rank = 0

    if comm is not None:
        rank = comm.Get_rank()
    checkpoint_status = 0

    dv = eos(q=q)

    have_exact = False

    if ((do_status is True or do_viz is True) and exact_soln is not None):
        have_exact = True
        expected_state = exact_soln(t=t, x_vec=nodes, eos=eos)

    if do_status is True:
        #        if constant_cfl is False:
        #            current_cfl = get_inviscid_cfl(discr=discr, q=q,
        #                                           eos=eos, dt=dt)
        statusmesg = make_status_message(t=t, step=step, dt=dt,
                                         cfl=cfl, dv=dv)
        if have_exact is True:
            max_errors = compare_states(red_state=q, blue_state=expected_state)
            statusmesg += f"\n------   Err({max_errors})"
            if rank == 0:
                logger.info(statusmesg)

            maxerr = np.max(max_errors)
            if maxerr > exittol:
                logger.error("Solution failed to follow expected result.")
                checkpoint_status = 1

    if do_viz:
        dim = discr.dim
        io_fields = make_io_fields(dim, q, dv, eos)
        if have_exact is True:
            io_fields.append(("exact_soln", expected_state))
            result_resid = q - expected_state
            io_fields.append(("residual", result_resid))
        make_output_dump(visualizer, basename=vizname, io_fields=io_fields,
                         comm=comm, step=step, t=t, overwrite=True)

    return checkpoint_status