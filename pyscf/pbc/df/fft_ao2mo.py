#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

r'''
Integral transformation with FFT

(ij|kl) = \int dr1 dr2 i*(r1) j(r1) v(r12) k*(r2) l(r2)
        = (ij|G) v(G) (G|kl)

i*(r) j(r) = 1/N \sum_G e^{iGr}  (G|ij)
           = 1/N \sum_G e^{-iGr} (ij|G)

"forward" FFT:
    (G|ij) = \sum_r e^{-iGr} i*(r) j(r) = fft[ i*(r) j(r) ]
"inverse" FFT:
    (ij|G) = \sum_r e^{iGr} i*(r) j(r) = N * ifft[ i*(r) j(r) ]
           = conj[ \sum_r e^{-iGr} j*(r) i(r) ]
'''

import numpy
from pyscf import lib
from pyscf import ao2mo
from pyscf.ao2mo.incore import iden_coeffs
from pyscf.pbc import tools
from pyscf.pbc.lib.kpts_helper import is_zero, gamma_point


def get_eri(mydf, kpts=None, compact=False):
    cell = mydf.cell
    kptijkl = _format_kpts(kpts)
    kpti, kptj, kptk, kptl = kptijkl
    q = kptj - kpti
    coulG = tools.get_coulG(cell, q, gs=mydf.gs)
    coords = cell.gen_uniform_grids(mydf.gs)
    max_memory = mydf.max_memory - lib.current_memory()[0]

####################
# gamma point, the integral is real and with s4 symmetry
    if gamma_point(kptijkl):
        #:ao_pairs_G = get_ao_pairs_G(mydf, kptijkl[:2], q, compact=compact)
        #:ao_pairs_G *= numpy.sqrt(coulG).reshape(-1,1)
        #:eri = lib.dot(ao_pairs_G.T, ao_pairs_G, cell.vol/ngs**2)
        ao = mydf._numint.eval_ao(cell, coords, kpti)[0]
        ao = numpy.asarray(ao.T, order='C')
        eri = _contract_compact(mydf, (ao,ao), coulG, max_memory=max_memory)
        if not compact:
            nao = cell.nao_nr()
            eri = ao2mo.restore(1, eri, nao).reshape(nao**2,nao**2)
        return eri

####################
# aosym = s1, complex integrals
    else:
        #:ao_pairs_G = get_ao_pairs_G(mydf, kptijkl[:2], q, compact=False)
        #:# ao_pairs_invG = rho_kl(-(G+k_ij)) = conj(rho_lk(G+k_ij)).swap(r,s)
        #:#=get_ao_pairs_G(mydf, [kptl,kptk], q, compact=False).transpose(0,2,1).conj()
        #:ao_pairs_invG = get_ao_pairs_G(mydf, -kptijkl[2:], q, compact=False).conj()
        #:ao_pairs_G *= coulG.reshape(-1,1)
        #:eri = lib.dot(ao_pairs_G.T, ao_pairs_invG, cell.vol/ngs**2)
        if is_zero(kpti-kptl) and is_zero(kptj-kptk):
            if is_zero(kpti-kptj):
                aoi = mydf._numint.eval_ao(cell, coords, kpti)[0]
                aoi = aoj = numpy.asarray(aoi.T, order='C')
            else:
                aoi, aoj = mydf._numint.eval_ao(cell, coords, kptijkl[:2])
                aoi = numpy.asarray(aoi.T, order='C')
                aoj = numpy.asarray(aoj.T, order='C')
            aos = (aoi, aoj, aoj, aoi)
        else:
            aos = mydf._numint.eval_ao(cell, coords, kptijkl)
            aos = [numpy.asarray(x.T, order='C') for x in aos]
        fac = numpy.exp(-1j * numpy.dot(coords, q))
        max_memory = max_memory - aos[0].nbytes*4*1e-6
        eri = _contract_plain(mydf, aos, coulG, fac, max_memory=max_memory)
        return eri


def general(mydf, mo_coeffs, kpts=None, compact=False):
    '''General MO integral transformation'''
    cell = mydf.cell
    kptijkl = _format_kpts(kpts)
    kpti, kptj, kptk, kptl = kptijkl
    if isinstance(mo_coeffs, numpy.ndarray) and mo_coeffs.ndim == 2:
        mo_coeffs = (mo_coeffs,) * 4
    mo_coeffs = [numpy.asarray(mo, order='F') for mo in mo_coeffs]
    allreal = not any(numpy.iscomplexobj(mo) for mo in mo_coeffs)
    q = kptj - kpti
    coulG = tools.get_coulG(cell, q, gs=mydf.gs)
    coords = cell.gen_uniform_grids(mydf.gs)
    max_memory = mydf.max_memory - lib.current_memory()[0]

    if gamma_point(kptijkl) and allreal:
        ao = mydf._numint.eval_ao(cell, coords, kpti)[0]
        if ((iden_coeffs(mo_coeffs[0], mo_coeffs[2]) and
             iden_coeffs(mo_coeffs[1], mo_coeffs[3]))):
            moiT = mojT = numpy.asarray(lib.dot(mo_coeffs[0].T,ao.T), order='C')
            ao = None
            max_memory = max_memory - moiT.nbytes*1e-6
            eri = _contract_compact(mydf, (moiT,mojT), coulG, max_memory=max_memory)
            if not compact:
                nmo = moiT.shape[0]
                eri = ao2mo.restore(1, eri, nmo).reshape(nmo**2,nmo**2)
        else:
            mos = [numpy.asarray(lib.dot(c.T, ao.T), order='C') for c in mo_coeffs]
            ao = None
            fac = numpy.array(1.)
            max_memory = max_memory - sum([x.nbytes for x in mos])*1e-6
            eri = _contract_plain(mydf, mos, coulG, fac, max_memory=max_memory).real
        return eri

    else:
        aos = mydf._numint.eval_ao(cell, coords, kptijkl)
        mos = [numpy.asarray(lib.dot(c.T, aos[i].T), order='C')
               for i,c in enumerate(mo_coeffs)]
        aos = None
        fac = numpy.exp(-1j * numpy.dot(coords, q))
        max_memory = max_memory - sum([x.nbytes for x in mos])*1e-6
        eri = _contract_plain(mydf, mos, coulG, fac, max_memory=max_memory)
        return eri


def _contract_compact(mydf, mos, coulG, max_memory):
    cell = mydf.cell
    moiT, mokT = mos
    nmoi, ngs = moiT.shape
    nmok = mokT.shape[0]
    wcoulG = coulG * (cell.vol/ngs)

    def fill(moT, i0, i1, buf):
        npair = i1*(i1+1)//2 - i0*(i0+1)//2
        out = numpy.ndarray((npair,ngs), dtype=buf.dtype, buffer=buf)
        ij = 0
        for i in range(i0, i1):
            numpy.einsum('p,jp->jp', moT[i], moT[:i+1], out=out[ij:ij+i+1])
            ij += i + 1
        return out

    eri = numpy.empty((nmoi*(nmoi+1)//2,nmok*(nmok+1)//2))
    blksize = int(min(max(nmoi,nmok), (max_memory*1e6/8 - eri.size)/2/ngs+1))
    buf = numpy.empty((blksize,ngs))
    for p0, p1 in lib.prange_tril(0, nmoi, blksize):
        mo_pairs_G = tools.fft(fill(moiT, p0, p1, buf), mydf.gs)
        mo_pairs_G*= wcoulG
        v = tools.ifft(mo_pairs_G, mydf.gs)
        vR = numpy.asarray(v.real, order='C')
        for q0, q1 in lib.prange_tril(0, nmok, blksize):
            mo_pairs = numpy.asarray(fill(mokT, q0, q1, buf), order='C')
            eri[p0*(p0+1)//2:p1*(p1+1)//2,
                q0*(q0+1)//2:q1*(q1+1)//2] = lib.ddot(vR, mo_pairs.T)
        v = None
    return eri

def _contract_plain(mydf, mos, coulG, phase, max_memory):
    cell = mydf.cell
    moiT, mojT, mokT, molT = mos
    nmoi, nmoj, nmok, nmol = [x.shape[0] for x in mos]
    ngs = moiT.shape[1]
    wcoulG = coulG * (cell.vol/ngs)
    dtype = numpy.result_type(phase, *mos)
    eri = numpy.empty((nmoi*nmoj,nmok*nmol), dtype=dtype)

    blksize = int(min(max(nmoi,nmok), (max_memory*1e6/16 - eri.size)/2/ngs/max(nmoj,nmol)+1))
    buf0 = numpy.empty((blksize,max(nmoj,nmol),ngs), dtype=dtype)
    buf1 = numpy.ndarray((blksize,nmoj,ngs), dtype=dtype, buffer=buf0)
    buf2 = numpy.ndarray((blksize,nmol,ngs), dtype=dtype, buffer=buf0)
    for p0, p1 in lib.prange(0, nmoi, blksize):
        mo_pairs = numpy.einsum('ig,jg->ijg', moiT[p0:p1].conj()*phase,
                                mojT, out=buf1[:p1-p0])
        mo_pairs_G = tools.fft(mo_pairs.reshape(-1,ngs), mydf.gs)
        mo_pairs = None
        mo_pairs_G*= wcoulG
        v = tools.ifft(mo_pairs_G, mydf.gs)
        mo_pairs_G = None
        v *= phase.conj()
        if dtype == numpy.double:
            v = numpy.asarray(v.real, order='C')
        for q0, q1 in lib.prange(0, nmok, blksize):
            mo_pairs = numpy.einsum('ig,jg->ijg', mokT[q0:q1].conj(),
                                    molT, out=buf2[:q1-q0])
            eri[p0*nmoj:p1*nmoj,q0*nmol:q1*nmol] = lib.dot(v, mo_pairs.reshape(-1,ngs).T)
        v = None
    return eri


def get_ao_pairs_G(mydf, kpts=numpy.zeros((2,3)), q=None, shls_slice=None,
                   compact=False):
    '''Calculate forward (G|ij) FFT of all AO pairs.

    Returns:
        ao_pairs_G : 2D complex array
            For gamma point, the shape is (ngs, nao*(nao+1)/2); otherwise the
            shape is (ngs, nao*nao)
    '''
    if kpts is None: kpts = numpy.zeros((2,3))
    cell = mydf.cell
    kpts = numpy.asarray(kpts)
    coords = cell.gen_uniform_grids(mydf.gs)
    ngs = len(coords)

    if shls_slice is None:
        i0, i1 = j0, j1 = (0, cell.nao_nr())
    else:
        ish0, ish1, jsh0, jsh1 = shls_slice
        ao_loc = cell.ao_loc_nr()
        i0 = ao_loc[ish0]
        i1 = ao_loc[ish1]
        j0 = ao_loc[jsh0]
        j1 = ao_loc[jsh1]

    def trans(aoi, aoj, fac=1):
        if id(aoi) == id(aoj):
            aoi = aoj = numpy.asarray(aoi.T, order='C')
        else:
            aoi = numpy.asarray(aoi.T, order='C')
            aoj = numpy.asarray(aoj.T, order='C')
        ni = aoi.shape[0]
        nj = aoj.shape[0]
        ao_pairs_G = numpy.empty((ni,nj,ngs), dtype=numpy.complex128)
        for i in range(ni):
            ao_pairs_G[i] = tools.fft(fac * aoi[i].conj() * aoj, mydf.gs)
        ao_pairs_G = ao_pairs_G.reshape(-1,ngs).T
        return ao_pairs_G

    if compact and gamma_point(kpts):  # gamma point
        ao = mydf._numint.eval_ao(cell, coords, kpts[:1])[0]
        ao = numpy.asarray(ao.T, order='C')
        npair = i1*(i1+1)//2 - i0*(i0+1)//2
        ao_pairs_G = numpy.empty((npair,ngs), dtype=numpy.complex128)
        ij = 0
        for i in range(i0, i1):
            ao_pairs_G[ij:ij+i+1] = tools.fft(ao[i] * ao[:i+1], mydf.gs)
            ij += i + 1
        ao_pairs_G = ao_pairs_G.T

    elif is_zero(kpts[0]-kpts[1]):
        ao = mydf._numint.eval_ao(cell, coords, kpts[:1])[0]
        ao_pairs_G = trans(ao[:,i0:i1], ao[:,j0:j1])

    else:
        if q is None:
            q = kpts[1] - kpts[0]
        aoi, aoj = mydf._numint.eval_ao(cell, coords, kpts[:2])
        fac = numpy.exp(-1j * numpy.dot(coords, q))
        ao_pairs_G = trans(aoi[:,i0:i1], aoj[:,j0:j1], fac)

    return ao_pairs_G

def get_mo_pairs_G(mydf, mo_coeffs, kpts=numpy.zeros((2,3)), q=None, compact=False):
    '''Calculate forward (G|ij) FFT of all MO pairs.

    Args:
        mo_coeff: length-2 list of (nao,nmo) ndarrays
            The two sets of MO coefficients to use in calculating the
            product |ij).

    Returns:
        mo_pairs_G : (ngs, nmoi*nmoj) ndarray
            The FFT of the real-space MO pairs.
    '''
    if kpts is None: kpts = numpy.zeros((2,3))
    cell = mydf.cell
    kpts = numpy.asarray(kpts)
    coords = cell.gen_uniform_grids(mydf.gs)
    nmoi = mo_coeffs[0].shape[1]
    nmoj = mo_coeffs[1].shape[1]
    ngs = len(coords)

    def trans(aoi, aoj, fac=1):
        if id(aoi) == id(aoj) and iden_coeffs(mo_coeffs[0], mo_coeffs[1]):
            moi = moj = numpy.asarray(lib.dot(mo_coeffs[0].T,aoi.T), order='C')
        else:
            moi = numpy.asarray(lib.dot(mo_coeffs[0].T, aoi.T), order='C')
            moj = numpy.asarray(lib.dot(mo_coeffs[1].T, aoj.T), order='C')
        mo_pairs_G = numpy.empty((nmoi,nmoj,ngs), dtype=numpy.complex128)
        for i in range(nmoi):
            mo_pairs_G[i] = tools.fft(fac * moi[i].conj() * moj, mydf.gs)
        mo_pairs_G = mo_pairs_G.reshape(-1,ngs).T
        return mo_pairs_G

    if gamma_point(kpts):  # gamma point, real
        ao = mydf._numint.eval_ao(cell, coords, kpts[:1])[0]
        if compact and iden_coeffs(mo_coeffs[0], mo_coeffs[1]):
            mo = numpy.asarray(lib.dot(mo_coeffs[0].T, ao.T), order='C')
            npair = nmoi*(nmoi+1)//2
            mo_pairs_G = numpy.empty((npair,ngs), dtype=numpy.complex128)
            ij = 0
            for i in range(nmoi):
                mo_pairs_G[ij:ij+i+1] = tools.fft(mo[i].conj() * mo[:i+1], mydf.gs)
                ij += i + 1
            mo_pairs_G = mo_pairs_G.T
        else:
            mo_pairs_G = trans(ao, ao)

    elif is_zero(kpts[0]-kpts[1]):
        ao = mydf._numint.eval_ao(cell, coords, kpts[:1])[0]
        mo_pairs_G = trans(ao, ao)

    else:
        if q is None:
            q = kpts[1] - kpts[0]
        aoi, aoj = mydf._numint.eval_ao(cell, coords, kpts)
        fac = numpy.exp(-1j * numpy.dot(coords, q))
        mo_pairs_G = trans(aoi, aoj, fac)

    return mo_pairs_G

def _format_kpts(kpts):
    if kpts is None:
        kptijkl = numpy.zeros((4,3))
    else:
        kpts = numpy.asarray(kpts)
        if kpts.size == 3:
            kptijkl = numpy.vstack([kpts]*4).reshape(4,3)
        else:
            kptijkl = kpts.reshape(4,3)
    return kptijkl


if __name__ == '__main__':
    import pyscf.pbc.gto as pgto
    from pyscf.pbc import df

    L = 5.
    n = 5
    cell = pgto.Cell()
    cell.a = numpy.diag([L,L,L])
    cell.gs = numpy.array([n,n,n])

    cell.atom = '''He    3.    2.       3.
                   He    1.    1.       1.'''
    #cell.basis = {'He': [[0, (1.0, 1.0)]]}
    #cell.basis = '631g'
    #cell.basis = {'He': [[0, (2.4, 1)], [1, (1.1, 1)]]}
    cell.basis = 'ccpvdz'
    cell.verbose = 0
    cell.build(0,0)

    nao = cell.nao_nr()
    numpy.random.seed(1)
    kpts = numpy.random.random((4,3))
    kpts[3] = -numpy.einsum('ij->j', kpts[:3])
    with_df = df.FFTDF(cell)
    with_df.kpts = kpts
    mo =(numpy.random.random((nao,nao)) +
         numpy.random.random((nao,nao))*1j)
    eri = with_df.get_eri(kpts).reshape((nao,)*4)
    eri0 = numpy.einsum('pjkl,pi->ijkl', eri , mo.conj())
    eri0 = numpy.einsum('ipkl,pj->ijkl', eri0, mo       )
    eri0 = numpy.einsum('ijpl,pk->ijkl', eri0, mo.conj())
    eri0 = numpy.einsum('ijkp,pl->ijkl', eri0, mo       ).reshape(nao**2,-1)
    eri1 = with_df.ao2mo(mo, kpts)
    print(abs(eri1-eri0).sum())
