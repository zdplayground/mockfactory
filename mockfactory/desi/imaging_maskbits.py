"""
Script to apply imaging maskbits.
This example can be run with srun -n 16 python imaging_maskbits.py.
"""

import os
import logging

import fitsio
import numpy as np
from astropy import wcs
from mpi4py import MPI

from desiutil import brick


logger = logging.getLogger('maskbits')


def get_maskbits(ra, dec, maskbits_fn='/global/cfs/cdirs/cosmo/data/legacysurvey/dr9/{region}/coadd/{brickname:.3s}/{brickname}/legacysurvey-{brickname}-maskbits.fits.fz', dtype='i2', mpicomm=MPI.COMM_WORLD):
    """
    Return value of bit mask at input RA/Dec, expected to be scattered on all MPI processes.
    Based on Rongpu Zhou's code:
    https://github.com/rongpu/desi-examples/blob/master/bright_star_mask/read_pixel_maskbit.py

    **WANRING:** take care to split correctly ra, dec accross the rank otherwise the code is unusable

    Parameters
    ----------
    ra : array
        Right ascension.

    dec : array
        Declination.

    maskbits_fn : str
        Path to maskbits files, with keywords 'region' and 'brickname'.

    dtype : np.dtype, str, default='i2'
        Output type.

    mpicomm : MPI communicator, default=None
        The current MPI communicator.
    """
    def _dict_to_array(data):
        """
        Return dict as numpy array.

        Parameters
        ----------
        data : dict
            Data dictionary of name: array.

        Returns
        -------
        array : array
        """
        array = [(name, data[name]) for name in data]
        array = np.empty(array[0][1].shape[0], dtype=[(name, col.dtype, col.shape[1:]) for name, col in array])
        for name in data: array[name] = data[name]
        return array

    def get_brick_maskbits(maskbits_fn, ra, dec):
        """Extract maskbit associated to a (RA, Dec) position from a legacy imaging brick."""
        if os.path.isfile(maskbits_fn):
            # Read data and header
            maskbits_img, header = fitsio.read(maskbits_fn, header=True)

            # Convert ra, dec coordinates to brick coordinates
            coadd_x, coadd_y = wcs.WCS(header).wcs_world2pix(ra, dec, 0)
            coadd_x, coadd_y = np.round(coadd_x).astype(int), np.round(coadd_y).astype(int)

            # Extract mask information
            maskbits = maskbits_img[coadd_y, coadd_x]

        else:
            # raise ValueError
            # Sometimes we can have objects outside DR9 footprint:
            # remove these objects setting maskbits 0 (NPRIMARY pixel)
            maskbits = 2**0 * np.ones(ra.size, dtype=dtype)

        return maskbits

    ra, dec = np.asarray(ra), np.asarray(dec)
    maskbits = np.ones_like(ra, dtype='i2')

    # load bricks class
    bricks = brick.Bricks()

    # create unique identification as index column
    cumsize = np.cumsum([0] + mpicomm.allgather(ra.size))[mpicomm.rank]
    index = cumsize + np.arange(ra.size)
    data = _dict_to_array({'ra': ra, 'dec': dec, 'brickname': bricks.brickname(ra, dec), 'brickid': bricks.brickid(ra, dec), 'maskbits': maskbits, 'index': index})

    # since we want to parrallelize around the brickid
    # we do not expect that the number of particles is the same as the input on each rank
    # use out argument in mpsort.sort function
    unique_brickname, brick_counts = np.unique(np.concatenate(mpicomm.allgather(data['brickid'])), return_counts=True)
    # number of brick per rank
    nbr_bricks = unique_brickname.size // mpicomm.size
    if mpicomm.rank < (unique_brickname.size % mpicomm.size):
        nbr_bricks += 1
    nbr_bricks = mpicomm.allgather(nbr_bricks)
    # number of particles (after sort) desired per rank
    nbr_particles = np.sum(brick_counts[int(np.sum(nbr_bricks[:mpicomm.rank])): int(np.sum(nbr_bricks[:mpicomm.rank])) + nbr_bricks[mpicomm.rank]])

    # sort data to have same number of bricks in each available rank
    import mpsort
    data_tmp = np.empty(nbr_particles, dtype=data.dtype)
    mpsort.sort(data, orderby='brickid', out=data_tmp)

    if mpicomm.rank == 0:
        logger.info(f'Nbr of bricks to read per rank = {np.min(nbr_bricks)}/{np.max(nbr_bricks)} (min/max)')

    for brickname in np.unique(data_tmp['brickname']):
        mask_brick = data_tmp['brickname'] == brickname
        region = 'north' if bricks.brick_radec(data_tmp['ra'][mask_brick][0], data_tmp['dec'][mask_brick][0])[1] > 32.375 else 'south'
        data_tmp['maskbits'][mask_brick] = get_brick_maskbits(maskbits_fn.format(region=region, brickname=brickname), data_tmp['ra'][mask_brick], data_tmp['dec'][mask_brick])

    data = np.empty(data.size, dtype=data_tmp.dtype)
    mpsort.sort(data_tmp, orderby='index', out=data)
    # test if we find the corret inital order
#    assert np.all(data['index'] == index)
#    maskbits = data['maskbits']

    return maskbits


if __name__ == '__main__':

    from mockfactory import RandomCutskyCatalog, setup_logging

    mpicomm = MPI.COMM_WORLD

    setup_logging()

    if mpicomm.rank == 0:
        logger.info('Run simple example to illustrate how to apply DR9 maskbits.')

    # Generate example cutsky catalog, scattered on all processes
    cutsky = RandomCutskyCatalog(rarange=(20., 30.), decrange=(-0.5, 2.), size=10000, seed=44, mpicomm=mpicomm)
    start = MPI.Wtime()

    ra, dec = cutsky['RA'], cutsky['DEC']
    if mpicomm.rank == 1:
        # to test when empty catalog is given with MPI
        ra, dec = [], []

    maskbits = get_maskbits(ra, dec, mpicomm=mpicomm)
    if mpicomm.rank == 0:
        logger.info(f'Apply DR9 maskbits done in {MPI.Wtime() - start:2.2f} s.')
