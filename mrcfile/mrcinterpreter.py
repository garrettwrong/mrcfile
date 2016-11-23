# Copyright (c) 2016, Science and Technology Facilities Council
# This software is distributed under a BSD licence. See LICENSE.txt.
"""
mrcfile
-------

A pure Python implementation of the MRC2014 file format.

The MRC2014 format was described in the Journal of Structural Biology:
http://dx.doi.org/10.1016/j.jsb.2015.04.002

The format specification is available on the CCP-EM website:
http://www.ccpem.ac.uk/mrc_format/mrc2014.php

TODO: usage examples

>>> 1
1

>>> mrc
MrcFile('doc_test.mrc')

"""

# TODO: main improvements to make:
# - separate file opening from file interpretation - different classes, or a function which opens the file first? try to be flexible enough to deal with anonymous mmaps for testing, gzip files etc...
# - ability to create a large empty file to be filled piecemeal
# - handle crystallographic files sensibly - different mx & nx values etc
# IMOD - header correctly calculates pixel size as cella.x / mx, but 3dmod
# gives distances assuming X spacing applies in all dimensions, and applies it
# on a map of nx * ny * nz pixels (where it is probably wrong since it's
# calculated from mx, not nx).

# Other format specifications:
# http://www.ccp4.ac.uk/html/maplib.html (CCP4 version)
# http://bio3d.colorado.edu/imod/doc/mrc_format.txt (IMOD version)
# http://www.2dx.unibas.ch/documentation/mrc-software/fei-extended-mrc-format-not-used-by-2dx

# Import Python 3 features for future-proofing
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import os

import numpy as np

from .dtypes import VOXEL_SIZE_DTYPE
import mrcfile.utils as utils


# TODO: this is still using mmap for now! needs making more flexible
class MrcInterpreter(object):
    
    """An object representing an MRC / CCP4 map file.
    
    The header and data of the file are presented as numpy arrays.
    
    Note that the data array is mapped directly from the underlying file. If you
    keep a reference to it and change the data values, the file will still be
    changed even if this MrcFile object has been closed. To try to prevent this,
    the 'writeable' flag is set to False on the old data array whenever it is
    closed or replaced with a new array.
    
    Usage: TODO:
    
    """
    
    def __init__(self, **kwargs):
        """
        Initialiser. This is where we read the file header and data.
        
        Header values are stored in a structured numpy array. To get numeric
        values from them it might be necessary to use the item() method.
        
        TODO: finish
        
        """
        super(MrcInterpreter, self).__init__(**kwargs)
        
        if 'w' in self._mode:
            # New file. Create a default header and truncate the file to the
            # standard header size (1024 bytes).
            self.header = utils.create_default_header()
            self._file.truncate(self.header.nbytes)
        else:
            # Existing file. Read the header.
            self._read_header()
        
        # Now we have a header (default or from the file) we can read the
        # extended header and data arrays (which will be empty if this is a
        # new file)
        self._read_extended_header()
        self._read_data()
    
    @property
    def extended_header(self):
        """Get the file's extended header as a numpy array.
        
        By default the dtype of the extended header array is void (raw data,
        dtype 'V'). If the actual data type of the extended header is known, the
        dtype of the array can be changed to match.
        
        The extended header may be modified in place. To replace it completely,
        call set_extended_header().
        
        Note that the file's entire data block must be moved if the extended
        header size changes. Setting a new extended header can therefore be
        very time consuming with large files.
        """
        return self.__extended_header
    
    def set_extended_header(self, extended_header):
        """Replace the file's extended header.
        
        Note that the file's entire data block must be moved if the extended
        header size changes. Setting a new extended header can therefore be
        very time consuming with large files, if the new extended header
        occupies a different number of bytes than the previous one.
        """
        if self._read_only:
            raise ValueError('This file is read-only')
        if extended_header.nbytes != self.__extended_header.nbytes:
            data_copy = self.__data.copy()
            self._close_memmap()
            self.__extended_header = extended_header
            self.header.nsymbt = extended_header.nbytes
            header_nbytes = self.header.nbytes + extended_header.nbytes
            self._file.truncate(header_nbytes + data_copy.nbytes)
            self._open_memmap(data_copy.dtype, data_copy.shape)
            np.copyto(self.__data, data_copy)
        else:
            self.__extended_header = extended_header
    
    @property
    def data(self):
        """Get the file's current data block as a numpy array.
        
        The data is opened as a memory map to the file on disk, meaning that
        slices of data will be fetched from disk only when requested. This
        allows parts of very large files to be accessed easily and quickly.
        
        The data values can be modified and will automatically be written to
        disk (unless the file is open in read-only mode). However, after
        changing any values the header statistics might be incorrect. Call
        update_header_stats() to update them if required -- this is usually a
        good idea, but can take a long time for large files.
        
        Returns:
            The file's data block, as a numpy memmap array.
        """
        return self.__data
    
    def set_data(self, data):
        """Replace the file's data.
        
        This replaces the current data with a copy of the given array, and
        updates the header to match the new data dimensions. The data statistics
        (min, max, mean and rms) stored in the header will also be updated.
        """
        if self._read_only:
            raise ValueError('This file is read-only')
        
        # Copy the header and update it from the data
        # We use a copy so the current header and data will remain unchanged
        # if the new data is invalid and an exception is raised
        new_header = self.header.copy()
        utils.update_header_from_data(new_header, data)
        if data.size > 0:
            utils.update_header_stats(new_header, data)
        else:
            utils.reset_header_stats(new_header)
        
        # The dtype of the new memmap array might not be the same as the given
        # data array. For example if an array of unsigned bytes is given, they
        # should be written in mode 6 as unsigned 16-bit ints to avoid data
        # loss. So, we construct a new dtype to use for the file's data array.
        mode = new_header.mode
        dtype = utils.dtype_from_mode(mode).newbyteorder(mode.dtype.byteorder)
        
        # Ensure we can use 'safe' casting to copy the data into the file.
        # This should guarantee we are not accidentally performing a narrowing
        # type conversion and potentially losing data. If this assertion fails
        # there is probably an error in the logic which converts MRC modes to
        # and from dtypes.
        assert np.can_cast(data, dtype, casting='safe')
        
        # At this point, we know the data is valid, so we go ahead with the swap
        # First, close the old memmap and replace the header with the new one
        self._close_memmap()
        self.header = new_header
        
        # Next, truncate the file to the new size (this should be safe to do
        # whether the new size is smaller, greater or the same as the old size)
        header_nbytes = self.header.nbytes + self.extended_header.nbytes
        self._file.truncate(header_nbytes + data.nbytes)
        
        # Now, open a new memmap of the correct shape, size and dtype
        self._open_memmap(dtype, data.shape)
        
        # Finally, copy the new data into the memmap
        np.copyto(self.__data, data, casting='safe')
    
    @property
    def voxel_size(self):
        """Get or set the voxel size in angstroms.
        
        The voxel size is returned as a structured numpy record array with three
        fields (x, y and z). Note that changing the voxel_size array in-place
        will *not* change the voxel size in the file.
        
        To set the voxel size, assign a new value to the voxel_size attribute.
        You may give a single number, a 3-tuple (x, y ,z) or a modified version
        of the voxel_size array. The following examples are all equivalent:
        
        >>> mrc
        
        >>> mrc.voxel_size = 1.0
        
        >>> mrc.voxel_size = (1.0, 1.0, 1.0)
        
        >>> vox_sizes = mrc.voxel_size
        >>> vox_sizes.x = 1.0
        >>> vox_sizes.y = 1.0
        >>> vox_sizes.z = 1.0
        >>> mrc.voxel_size = vox_sizes
        """
        x = self.header.cella.x / self.header.mx
        y = self.header.cella.y / self.header.my
        z = self.header.cella.z / self.header.mz
        sizes = np.rec.array((x, y, z), VOXEL_SIZE_DTYPE)
        sizes.flags.writeable = False
        return sizes
    
    @voxel_size.setter
    def voxel_size(self, voxel_size):
        try:
            # First, assume we have a single numeric value
            sizes = (float(voxel_size),) * 3
        except TypeError:
            try:
                # Not a single value. Next, if voxel_size is an array (as
                # produced by the voxel_size getter), item() gives a 3-tuple
                sizes = voxel_size.item()
            except AttributeError:
                # If the item() method doesn't exist, assume we have a 3-tuple
                sizes = voxel_size
        self._set_voxel_size(*sizes)
    
    def _set_voxel_size(self, x_size, y_size, z_size):
        """Set the voxel size for the file.
        
        Args:
            x_size: The voxel size in the X direction, in angstroms
            y_size: The voxel size in the Y direction, in angstroms
            z_size: The voxel size in the Z direction, in angstroms
        """
        self.header.cella.x = x_size * self.header.mx
        self.header.cella.y = y_size * self.header.my
        self.header.cella.z = z_size * self.header.mz
    
    def is_single_image(self):
        return self.data.ndim == 2
    
    def is_image_stack(self):
        return (self.data.ndim == 3
                and self.header.ispg == utils.IMAGE_STACK_SPACEGROUP)
    
    def is_volume(self):
        return (self.data.ndim == 3
                and self.header.ispg != utils.IMAGE_STACK_SPACEGROUP)
    
    def is_volume_stack(self):
        return self.data.ndim == 4
    
    def set_image_stack(self):
        if self.data.ndim != 3:
            raise ValueError('Only 3D data can be changed into an image stack')
        self.header.ispg = utils.IMAGE_STACK_SPACEGROUP
        self.header.mz = 1
    
    def set_volume(self):
        if self.data.ndim != 3:
            raise ValueError('Only 3D data can be changed into a volume')
        if self.is_image_stack():
            self.header.ispg = utils.VOLUME_SPACEGROUP
            self.header.mz = self.header.nz
    
    def close(self):
        """Flush any changes to disk and close the file."""
        if not self._file.closed:
            self.flush()
            self._close_memmap()
            self._file.close()
        self.header = None
    
    def flush(self):
        """Flush the header and data arrays to the file buffer."""
        if not self._read_only:
            self._update_header_from_data()
            self._write_header()
            
            # Flushing the file before the mmap makes the mmap flush faster
            self._file.flush()
            self.__data.flush()
            self._file.flush()
    
    def __repr__(self):
        return "MrcFile('{0}', mode='{1}')".format(self._file.name,
                                                   self._file.mode[:-1])
    
    def __enter__(self):
        """Called by the context manager at the start of a 'with' block.
        
        Returns:
            This object (self) since it represents the open file.
        """
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Called by the context manager at the end of a 'with' block.
        
        This ensures that the close() method is called.
        """
        self.close()
    
    def __del__(self):
        """Attempt to close the file when this object is garbage collected.
        
        It's better not to rely on this - instead, use a 'with' block or
        explicitly call the close() method.
        """
        try:
            self.close()
        except AttributeError:
            # Probably the file was never opened and self._file doesn't exist
            pass
    
    def _read_header(self):
        """Read the header from the file."""
        self.header = utils.read_header(self._file)
        self.header.flags.writeable = not self._read_only
    
    def _read_extended_header(self):
        """Read the extended header from the file.
        
        If there is no extended header, a zero-length array is assigned to the
        extended_header field.
        """
        ext_header_size = self.header.nsymbt
        if ext_header_size > 0:
            self._file.seek(self.header.nbytes, os.SEEK_SET)
            ext_header_bytes = self._file.read(ext_header_size)
            self.__extended_header = np.array(ext_header_bytes, dtype='V')
        else:
            self.__extended_header = np.array((), dtype='V')
        
        self.extended_header.flags.writeable = not self._read_only
    
    def _read_data(self):
        """Read the data block from the file.
        
        This method first calculates the parameters needed to read the data
        (block start position, endian-ness, file mode, array shape) and then
        opens the data as a numpy memmap array.
        """
        
        mode = self.header.mode
        dtype = utils.dtype_from_mode(mode).newbyteorder(mode.dtype.byteorder)
        
        # convert data dimensions from header into array shape
        nx = self.header.nx
        ny = self.header.ny
        nz = self.header.nz
        mz = self.header.mz
        ispg = self.header.ispg
        
        if utils.spacegroup_is_volume_stack(ispg):
            shape = (nz // mz, mz, ny, nx)
        elif ispg == utils.IMAGE_STACK_SPACEGROUP and nz == 1:
            # Use a 2D array for a single image
            shape = (ny, nx)
        else:
            shape = (nz, ny, nx)
            
        header_size = self.header.nbytes + self.header.nsymbt
        data_size = nx * ny * nz * dtype.itemsize
        
        #####################################
        # memmap version:
        #
        self._file.seek(0, os.SEEK_END)
        file_size = self._file.tell()
        assert file_size == header_size + data_size
        
        self._open_memmap(dtype, shape)
        
        ######################################
        # normal array version:
        #
#         self._seek_to_data_block()
#         data_str = self._file.read(data_size)
#         
#         # Make sure that we have read the whole file
#         assert not self._file.read()
#         
#         # Read data
#         self.data = np.ndarray(shape=shape, dtype=dtype, buffer=data_str)
    
    def _update_header_from_data(self):
        if self._read_only:
            raise ValueError('This file is read-only')
        utils.update_header_from_data(self.header, self.data)
    
    def _write_header(self):
        self._file.seek(0)
        self._file.write(self.header)
        self._file.write(self.extended_header)
    
    def _open_memmap(self, dtype, shape):
        """Open a new memmap array pointing at the file's data block."""
        acc_mode = 'r' if self._read_only else 'r+'
        header_nbytes = self.header.nbytes + self.header.nsymbt
        
        self._file.flush()
        self.__data = np.memmap(self._file,
                                dtype=dtype,
                                mode=acc_mode,
                                offset=header_nbytes,
                                shape=shape)
    
    def _close_memmap(self):
        """Delete the existing memmap array, if it exists.
        
        The array is flagged as read-only before deletion, so if a reference to
        it has been kept elsewhere, changes to it should no longer be able to
        change the file contents.
        """
        try:
            self.__data.flush()
            self.__data.flags.writeable = False
            del self.__data
        except AttributeError:
            pass
    
    def print_header(self):
        """Print the file header."""
        utils.print_header(self.header)
    
    def update_header_stats(self):
        """Update the header statistics with accurate values from the data."""
        utils.update_header_stats(self.header, self.data)
