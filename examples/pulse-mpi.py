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
import logging
from mpi4py import MPI
import numpy as np
from functools import partial
import pyopencl as cl

from meshmode.array_context import PyOpenCLArrayContext
from meshmode.dof_array import thaw
from meshmode.mesh import BTAG_ALL, BTAG_NONE  # noqa
from grudge.eager import EagerDGDiscretization
from grudge.shortcuts import make_visualizer

from mirgecom.euler import (
    inviscid_operator,
    #    split_conserved
)
from mirgecom.simutil import (
    inviscid_sim_timestep,
    create_parallel_grid,
    sim_checkpoint,
    ExactSolutionMismatch,
)

from mirgecom.io import make_init_message

from mirgecom.integrators import rk4_step
from mirgecom.steppers import advance_state
from mirgecom.boundary import (
    PrescribedBoundary,
    AdiabaticSlipBoundary
)
from mirgecom.initializers import (
    Lump,
    make_pulse
)
from mirgecom.eos import IdealSingleGas


def main(ctx_factory=cl.create_some_context):

    cl_ctx = ctx_factory()
    queue = cl.CommandQueue(cl_ctx)
    actx = PyOpenCLArrayContext(queue)

    logger = logging.getLogger(__name__)

    dim = 2
    nel_1d = 32
    order = 1
    exittol = 2e-2
    exittol = 100.0
    t_final = .0001
    current_cfl = 1.0
    vel = np.zeros(shape=(dim,))
    orig = np.zeros(shape=(dim,))
    #    vel[:dim] = 1.0
    current_dt = .00001
    current_t = 0
    eos = IdealSingleGas()
    initializer = Lump(center=orig, velocity=vel, rhoamp=0.0)
    casename = 'pulse'
    boundaries = {BTAG_ALL: PrescribedBoundary(initializer)}
    wall = AdiabaticSlipBoundary()
    boundaries = {BTAG_ALL: wall}
    constant_cfl = False
    nstatus = 10
    nviz = 10
    rank = 0
    checkpoint_t = current_t
    current_step = 0
    timestepper = rk4_step
    box_ll = -0.5
    box_ur = 0.5

    comm = MPI.COMM_WORLD
    nproc = comm.Get_size()
    rank = comm.Get_rank()
    num_parts = nproc

    global_nelements = 0
    local_nelements = 0
    from meshmode.mesh.generation import generate_regular_rect_mesh
    if num_parts > 1:
        generate_grid = partial(generate_regular_rect_mesh, a=(box_ll,) * dim,
                                b=(box_ur,) * dim, n=(nel_1d,) * dim)
        local_mesh, global_nelements = create_parallel_grid(comm, generate_grid)
    else:
        local_mesh = generate_regular_rect_mesh(
            a=(box_ll,) * dim, b=(box_ur,) * dim, n=(nel_1d,) * dim
        )
        global_nelements = local_mesh.nelements
    local_nelements = local_mesh.nelements

    discr = EagerDGDiscretization(
        actx, local_mesh, order=order, mpi_communicator=comm
    )
    nodes = thaw(actx, discr.nodes())
    current_state = initializer(0, nodes)
    #    cv = split_conserved(dim, current_state)
    #    cv.energy = cv.energy + make_pulse(amp=1.0, w=.2, r0=orig, r=nodes)
    current_state[1] = current_state[1] + make_pulse(amp=1.0, w=.1, r0=orig, r=nodes)

    visualizer = make_visualizer(discr, discr.order + 3
                                 if discr.dim == 2 else discr.order)
    initname = "pulse"
    eosname = eos.__class__.__name__
    init_message = make_init_message(dim=dim, order=order,
                                     nelements=local_nelements,
                                     global_nelements=global_nelements,
                                     dt=current_dt, t_final=t_final, nstatus=nstatus,
                                     nviz=nviz, cfl=current_cfl,
                                     constant_cfl=constant_cfl, initname=initname,
                                     eosname=eosname, casename=casename)
    if rank == 0:
        logger.info(init_message)

    get_timestep = partial(inviscid_sim_timestep, discr=discr, t=current_t,
                           dt=current_dt, cfl=current_cfl, eos=eos,
                           t_final=t_final, constant_cfl=constant_cfl)

    def my_rhs(t, state):
        return inviscid_operator(discr, q=state, t=t,
                                 boundaries=boundaries, eos=eos)

    def my_checkpoint(step, t, dt, state):
        return sim_checkpoint(discr, visualizer, eos, q=state,
                              vizname=casename, step=step,
                              t=t, dt=dt, nstatus=nstatus, nviz=nviz,
                              exittol=exittol, constant_cfl=constant_cfl, comm=comm)

    try:
        (current_step, current_t, current_state) = \
            advance_state(rhs=my_rhs, timestepper=timestepper,
                          checkpoint=my_checkpoint,
                          get_timestep=get_timestep, state=current_state,
                          t=current_t, t_final=t_final)
    except ExactSolutionMismatch as ex:
        current_step = ex.step
        current_t = ex.t
        current_state = ex.state

    #    if current_t != checkpoint_t:
    if rank == 0:
        logger.info("Checkpointing final state ...")
    my_checkpoint(current_step, t=current_t,
                  dt=(current_t - checkpoint_t),
                  state=current_state)

    if current_t - t_final < 0:
        raise ValueError("Simulation exited abnormally")


if __name__ == "__main__":
    logging.basicConfig(format="%(message)s", level=logging.INFO)

    main()

# vim: foldmethod=marker