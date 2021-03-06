#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
A simple example to run CASSCF calculation.
'''

import numpy
from pyscf import gto, scf, mcscf

mol = gto.M(
    atom = 'O 0 0 0; O 0 0 1.2',
    basis = 'ccpvdz',
    spin = 2)

myhf = scf.RHF(mol)
myhf.kernel()

# 6 orbitals, 8 electrons
mycas = mcscf.CASSCF(myhf, 6, 8)
mycas.kernel()

# Natural occupancy in CAS space, Mulliken population etc.
# See also 00-simple_casci.py for the instruction of the output of analyze()
# method
mycas.verbose = 4
mycas.analyze()
