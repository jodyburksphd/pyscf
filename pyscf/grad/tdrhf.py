#!/usr/bin/env python
# Copyright 2014-2018 The PySCF Developers. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#
# Ref:
# J. Chem. Phys. 117, 7433
#

import time
from functools import reduce
import numpy
from pyscf import lib
from pyscf.lib import logger
from pyscf.scf import rhf_grad
from pyscf.scf import cphf
from pyscf import __config__


#
# Given Y = 0, TDHF gradients (XAX+XBY+YBX+YAY)^1 turn to TDA gradients (XAX)^1
#
def kernel(td_grad, x_y, singlet=True, atmlst=None,
           max_memory=2000, verbose=logger.INFO):
    if isinstance(verbose, logger.Logger):
        log = verbose
    else:
        log = logger.Logger(td_grad.stdout, verbose)
    time0 = time.clock(), time.time()

    mol = td_grad.mol
    mf = td_grad._td._scf
    mo_coeff = mf.mo_coeff
    mo_energy = mf.mo_energy
    mo_occ = mf.mo_occ
    nao, nmo = mo_coeff.shape
    nocc = (mo_occ>0).sum()
    nvir = nmo - nocc
    x, y = x_y
    xpy = (x+y).reshape(nocc,nvir).T
    xmy = (x-y).reshape(nocc,nvir).T
    orbv = mo_coeff[:,nocc:]
    orbo = mo_coeff[:,:nocc]

    dvv = numpy.einsum('ai,bi->ab', xpy, xpy) + numpy.einsum('ai,bi->ab', xmy, xmy)
    doo =-numpy.einsum('ai,aj->ij', xpy, xpy) - numpy.einsum('ai,aj->ij', xmy, xmy)
    dmzvop = reduce(numpy.dot, (orbv, xpy, orbo.T))
    dmzvom = reduce(numpy.dot, (orbv, xmy, orbo.T))
    dmzoo = reduce(numpy.dot, (orbo, doo, orbo.T))
    dmzoo+= reduce(numpy.dot, (orbv, dvv, orbv.T))

    vj, vk = mf.get_jk(mol, (dmzoo, dmzvop+dmzvop.T, dmzvom-dmzvom.T), hermi=0)
    veff0doo = vj[0] * 2 - vk[0]
    wvo = reduce(numpy.dot, (orbv.T, veff0doo, orbo)) * 2
    if singlet:
        veff = vj[1] * 2 - vk[1]
    else:
        veff = -vk[1]
    veff0mop = reduce(numpy.dot, (mo_coeff.T, veff, mo_coeff))
    wvo -= numpy.einsum('ki,ai->ak', veff0mop[:nocc,:nocc], xpy) * 2
    wvo += numpy.einsum('ac,ai->ci', veff0mop[nocc:,nocc:], xpy) * 2
    veff = -vk[2]
    veff0mom = reduce(numpy.dot, (mo_coeff.T, veff, mo_coeff))
    wvo -= numpy.einsum('ki,ai->ak', veff0mom[:nocc,:nocc], xmy) * 2
    wvo += numpy.einsum('ac,ai->ci', veff0mom[nocc:,nocc:], xmy) * 2
    def fvind(x):  # For singlet, closed shell ground state
        dm = reduce(numpy.dot, (orbv, x.reshape(nvir,nocc), orbo.T))
        vj, vk = mf.get_jk(mol, (dm+dm.T))
        return reduce(numpy.dot, (orbv.T, vj*2-vk, orbo)).ravel()
    z1 = cphf.solve(fvind, mo_energy, mo_occ, wvo,
                    max_cycle=td_grad.cphf_max_cycle,
                    tol=td_grad.cphf_conv_tol)[0]
    z1 = z1.reshape(nvir,nocc)
    time1 = log.timer('Z-vector using CPHF solver', *time0)

    z1ao = reduce(numpy.dot, (orbv, z1, orbo.T))
    vj, vk = mf.get_jk(mol, z1ao, hermi=0)
    veff = vj * 2 - vk

    im0 = numpy.zeros((nmo,nmo))
    im0[:nocc,:nocc] = reduce(numpy.dot, (orbo.T, veff0doo+veff, orbo))
    im0[:nocc,:nocc]+= numpy.einsum('ak,ai->ki', veff0mop[nocc:,:nocc], xpy)
    im0[:nocc,:nocc]+= numpy.einsum('ak,ai->ki', veff0mom[nocc:,:nocc], xmy)
    im0[nocc:,nocc:] = numpy.einsum('ci,ai->ac', veff0mop[nocc:,:nocc], xpy)
    im0[nocc:,nocc:]+= numpy.einsum('ci,ai->ac', veff0mom[nocc:,:nocc], xmy)
    im0[nocc:,:nocc] = numpy.einsum('ki,ai->ak', veff0mop[:nocc,:nocc], xpy)*2
    im0[nocc:,:nocc]+= numpy.einsum('ki,ai->ak', veff0mom[:nocc,:nocc], xmy)*2

    zeta = lib.direct_sum('i+j->ij', mo_energy, mo_energy) * .5
    zeta[nocc:,:nocc] = mo_energy[:nocc]
    zeta[:nocc,nocc:] = mo_energy[nocc:]
    dm1 = numpy.zeros((nmo,nmo))
    dm1[:nocc,:nocc] = doo
    dm1[nocc:,nocc:] = dvv
    dm1[nocc:,:nocc] = z1
    dm1[:nocc,:nocc] += numpy.eye(nocc)*2 # for ground state
    im0 = reduce(numpy.dot, (mo_coeff, im0+zeta*dm1, mo_coeff.T))

    hcore_deriv = td_grad.hcore_generator(mol)
    s1 = td_grad.get_ovlp(mol)

    dmz1doo = z1ao + dmzoo
    oo0 = reduce(numpy.dot, (orbo, orbo.T))
    vj, vk = td_grad.get_jk(mol, (oo0, dmz1doo+dmz1doo.T, dmzvop+dmzvop.T,
                                  dmzvom-dmzvom.T))
    vj = vj.reshape(-1,3,nao,nao)
    vk = vk.reshape(-1,3,nao,nao)
    if singlet:
        vhf1 = vj * 2 - vk
    else:
        vhf1 = numpy.vstack((vj[:2]*2-vk[:2], -vk[2:]))
    time1 = log.timer('2e AO integral derivatives', *time1)

    if atmlst is None:
        atmlst = range(mol.natm)
    offsetdic = mol.offset_nr_by_atom()
    de = numpy.zeros((len(atmlst),3))
    for k, ia in enumerate(atmlst):
        shl0, shl1, p0, p1 = offsetdic[ia]

        # Ground state gradients
        h1ao = hcore_deriv(ia)
        h1ao[:,p0:p1]   += vhf1[0,:,p0:p1]
        h1ao[:,:,p0:p1] += vhf1[0,:,p0:p1].transpose(0,2,1)
        # oo0*2 for doubly occupied orbitals
        de[k] = numpy.einsum('xpq,pq->x', h1ao, oo0) * 2

        de[k] += numpy.einsum('xpq,pq->x', h1ao, dmz1doo)
        de[k] -= numpy.einsum('xpq,pq->x', s1[:,p0:p1], im0[p0:p1])
        de[k] -= numpy.einsum('xqp,pq->x', s1[:,p0:p1], im0[:,p0:p1])

        de[k] += numpy.einsum('xij,ij->x', vhf1[1,:,p0:p1], oo0[p0:p1])
        de[k] += numpy.einsum('xij,ij->x', vhf1[2,:,p0:p1], dmzvop[p0:p1,:]) * 2
        de[k] += numpy.einsum('xij,ij->x', vhf1[3,:,p0:p1], dmzvom[p0:p1,:]) * 2
        de[k] += numpy.einsum('xji,ij->x', vhf1[2,:,p0:p1], dmzvop[:,p0:p1]) * 2
        de[k] -= numpy.einsum('xji,ij->x', vhf1[3,:,p0:p1], dmzvom[:,p0:p1]) * 2

    log.timer('TDHF nuclear gradients', *time0)
    return de


class Gradients(rhf_grad.Gradients):

    cphf_max_cycle = getattr(__config__, 'grad_tdrhf_Gradients_cphf_max_cycle', 20)
    cphf_conv_tol = getattr(__config__, 'grad_tdrhf_Gradients_cphf_conv_tol', 1e-8)

    def __init__(self, td):
        self.verbose = td.verbose
        self.stdout = td.stdout
        self.mol = td.mol
        self._td = td
        self._scf = td._scf
        self.chkfile = td.chkfile
        self.max_memory = td.max_memory

        self.de = 0
        keys = set(('cphf_max_cycle', 'cphf_conv_tol'))
        self._keys = set(self.__dict__.keys()).union(keys)

    def dump_flags(self):
        log = logger.Logger(self.stdout, self.verbose)
        log.info('\n')
        log.info('******** LR %s gradients for %s ********',
                 self._td.__class__, self._td._scf.__class__)
        log.info('cphf_conv_tol = %g', self.cphf_conv_tol)
        log.info('cphf_max_cycle = %d', self.cphf_max_cycle)
        log.info('chkfile = %s', self.chkfile)
        log.info('max_memory %d MB (current use %d MB)',
                 self.max_memory, lib.current_memory()[0])
        log.info('\n')
        return self

    def grad_elec(self, xy, singlet, atmlst=None):
        return kernel(self, xy, singlet, atmlst, self.max_memory, self.verbose)

    def kernel(self, xy=None, state=1, singlet=None, atmlst=None):
        '''
        Args:
            state : int
                Excited state ID.  state = 1 means the first excited state.
        '''
        if state == 0:
            logger.warn(self, 'state=0 found in the input. '
                        'Gradients of ground state is computed.')
            return self._scf.nuc_grad_method().kernel(atmlst=atmlst)

        cput0 = (time.clock(), time.time())
        if xy is None: xy = self._td.xy[state-1]
        if singlet is None: singlet = self._td.singlet
        if atmlst is None: atmlst = range(self.mol.natm)
        self.check_sanity()
        de = self.grad_elec(xy, singlet, atmlst)
        self.de = de = de + self.grad_nuc(atmlst=atmlst)
        #self.de = de = de + self._scf.nuc_grad_method().kernel(atmlst=atmlst)

        logger.note(self, '--------------')
        logger.note(self, '           x                y                z')
        for k, ia in enumerate(atmlst):
            logger.note(self, '%d %s  %15.9f  %15.9f  %15.9f', ia,
                        self.mol.atom_symbol(ia), de[k,0], de[k,1], de[k,2])
        logger.note(self, '--------------')
        logger.timer(self, 'TD gradients', *cput0)
        return self.de


if __name__ == '__main__':
    from pyscf import gto
    from pyscf import scf
    from pyscf import dft
    from pyscf import tddft
    mol = gto.Mole()
    mol.verbose = 0
    mol.output = None

    mol.atom = [
        ['H' , (0. , 0. , 1.804)],
        ['F' , (0. , 0. , 0.)], ]
    mol.unit = 'B'
    mol.basis = '631g'
    mol.build()

    mf = scf.RHF(mol).run(conv_tol=1e-14)
    td = tddft.TDA(mf)
    td.nstates = 3
    e, z = td.kernel()
    tdg = Gradients(td)
    #tdg.verbose = 5
    g1 = tdg.kernel(z[0])
    print(g1)
    print(lib.finger(g1) - 0.18686561181358813)
#[[ 0  0  -2.67023832e-01]
# [ 0  0   2.67023832e-01]]
    td_solver = td.as_scanner()
    e1 = td_solver(mol.set_geom_('H 0 0 1.805; F 0 0 0', unit='B'))
    e2 = td_solver(mol.set_geom_('H 0 0 1.803; F 0 0 0', unit='B'))
    print(abs((e1[0]-e2[0])/.002 - g1[0,2]).max())

    mol.set_geom_('H 0 0 1.804; F 0 0 0', unit='B')
    td = tddft.TDDFT(mf)
    td.nstates = 3
    e, z = td.kernel()
    tdg = Gradients(td)
    g1 = tdg.kernel(state=1)
    print(g1)
    print(lib.finger(g1) - 0.18967687762609461)
# [[ 0  0  -2.71041021e-01]
#  [ 0  0   2.71041021e-01]]
    td_solver = td.as_scanner()
    e1 = td_solver(mol.set_geom_('H 0 0 1.805; F 0 0 0', unit='B'))
    e2 = td_solver(mol.set_geom_('H 0 0 1.803; F 0 0 0', unit='B'))
    print(abs((e1[0]-e2[0])/.002 - g1[0,2]).max())

    mol.set_geom_('H 0 0 1.804; F 0 0 0', unit='B')
    td = tddft.TDA(mf)
    td.nstates = 3
    td.singlet = False
    e, z = td.kernel()
    tdg = Gradients(td)
    g1 = tdg.kernel(state=1)
    print(g1)
    print(lib.finger(g1) - 0.19667995802487931)
# [[ 0  0  -2.81048403e-01]
#  [ 0  0   2.81048403e-01]]
    td_solver = td.as_scanner()
    e1 = td_solver(mol.set_geom_('H 0 0 1.805; F 0 0 0', unit='B'))
    e2 = td_solver(mol.set_geom_('H 0 0 1.803; F 0 0 0', unit='B'))
    print(abs((e1[0]-e2[0])/.002 - g1[0,2]).max())

    mol.set_geom_('H 0 0 1.804; F 0 0 0', unit='B')
    td = tddft.TDDFT(mf)
    td.nstates = 3
    td.singlet = False
    e, z = td.kernel()
    tdg = Gradients(td)
    g1 = tdg.kernel(state=1)
    print(g1)
    print(lib.finger(g1) - 0.20032088639558535)
# [[ 0  0  -2.86250870e-01]
#  [ 0  0   2.86250870e-01]]
    td_solver = td.as_scanner()
    e1 = td_solver(mol.set_geom_('H 0 0 1.805; F 0 0 0', unit='B'))
    e2 = td_solver(mol.set_geom_('H 0 0 1.803; F 0 0 0', unit='B'))
    print(abs((e1[0]-e2[0])/.002 - g1[0,2]).max())
