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
import numpy as np
import numpy.linalg as la  # noqa
import pyopencl as cl
import pyopencl.array as cla  # noqa

from grudge.eager import EagerDGDiscretization
from grudge.shortcuts import make_visualizer
from mirgecom.euler import get_inviscid_timestep
from mirgecom.euler import inviscid_operator
from mirgecom.euler import split_fields
from mirgecom.integrators import rk4_step
from meshmode.mesh import BTAG_ALL, BTAG_NONE  # noqa


def euler_flow_stepper(parameters, ctx_factory=cl.create_some_context):
    """
    Implements a generic time stepping loop for an inviscid flow.
    """
    cl_ctx = ctx_factory()
    queue = cl.CommandQueue(cl_ctx)

    logging.basicConfig(format="%(message)s", level=logging.INFO)
    logger = logging.getLogger(__name__)

    mesh = parameters['mesh']
    t = parameters['time']
    order = parameters['order']
    t_final = parameters['tfinal']
    initializer = parameters['initializer']
    exittol = parameters['exittol']
    casename = parameters['casename']
    boundaries = parameters['boundaries']
    eos = parameters['eos']
    cfl = parameters['cfl']
    dt = parameters['dt']
    constantcfl = parameters['constantcfl']
    nstepstatus = parameters['nstatus']

    if t_final <= t:
        return(0.0)

    rank = 0
    dim = mesh.dim
    istep = 0

    discr = EagerDGDiscretization(cl_ctx, mesh, order=order)
    nodes = discr.nodes().with_queue(queue)
    fields = initializer(0, nodes)
    sdt = get_inviscid_timestep(discr, fields, c=cfl, eos=eos)

    initname = initializer.__class__.__name__
    eosname = eos.__class__.__name__
    message = (
        f"Num {dim}d elements: {mesh.nelements}\n"
        f"Timestep:        {dt}\n"
        f"Final time:      {t_final}\n"
        f"Status freq:     {nstepstatus}\n"
        f"Initialization:  {initname}\n"
        f"EOS:             {eosname}"
    )
    logger.info(message)

    vis = make_visualizer(discr, discr.order + 3 if dim == 2 else discr.order)

    def write_soln():
        dv = eos(fields)
        expected_result = initializer(t, nodes)
        result_resid = fields - expected_result
        maxerr = [np.max(np.abs(result_resid[i].get())) for i in range(dim + 2)]
        mindv = [np.min(dvfld.get()) for dvfld in dv]
        maxdv = [np.max(dvfld.get()) for dvfld in dv]

        statusmsg = (
            f"Status: Step({istep}) Time({t})\n"
            f"------   P({mindv[0]},{maxdv[0]})\n"
            f"------   T({mindv[1]},{maxdv[1]})\n"
            f"------   dt,cfl = ({dt},{cfl})\n"
            f"------   Err({maxerr})"
        )
        logger.info(statusmsg)

        io_fields = split_fields(dim, fields)
        io_fields += eos.split_fields(dim, dv)
        io_fields.append(("exact_soln", expected_result))
        io_fields.append(("residual", result_resid))
        nameform = casename + "-{iorank:04d}-{iostep:04d}.vtu"
        visfilename = nameform.format(iorank=rank, iostep=istep)
        vis.write_vtk_file(visfilename, io_fields)

        return maxerr

    def rhs(t, w):
        return inviscid_operator(discr, w=w, t=t, boundaries=boundaries, eos=eos)

    while t < t_final:

        if constantcfl is True:
            dt = sdt
        else:
            cfl = dt / sdt

        if nstepstatus > 0:
            if istep % nstepstatus == 0:
                write_soln()

        fields = rk4_step(fields, t, dt, rhs)
        t += dt
        istep += 1

        sdt = get_inviscid_timestep(discr, fields, c=cfl, eos=eos)

    if nstepstatus > 0:
        logger.info("Writing final dump.")
        maxerr = max(write_soln())
    else:
        expected_result = initializer(t, nodes)
        result_resid = fields - expected_result
        maxerr = np.max(
            [
                np.max(
                    np.abs(
                        result_resid[i].get()
                    )
                ) for i in range(dim + 2)
            ]
        )

    if maxerr > exittol:
        raise ValueError("Solution failed to follow expected result.")

    logger.info("Goodbye!")
    return(maxerr)