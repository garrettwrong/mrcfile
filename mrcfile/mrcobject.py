# Copyright (c) 2016, Science and Technology Facilities Council
# This software is distributed under a BSD licence. See LICENSE.txt.
"""
mrcobject
---------

Module which exports the :class:`MrcObject` class.

Classes:
    :class:`MrcObject`: An object representing image or volume data in the MRC
    format.

"""

# Import Python 3 features for future-proofing
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from datetime import datetime

import numpy as np

from . import utils
from .dtypes import HEADER_DTYPE, VOXEL_SIZE_DTYPE
from .constants import (MAP_ID, MRC_FORMAT_VERSION, IMAGE_STACK_SPACEGROUP,
                        VOLUME_SPACEGROUP, VOLUME_STACK_SPACEGROUP)


class MrcObject(object):
    
    """An object representing image or volume data in the MRC format.
    
    The header, extended header and data are stored as numpy arrays and
    exposed as read-only attributes. To replace the data or extended header,
    call :meth:`set_data` or :meth:`set_extended_header`. The header cannot be
    replaced but can be modified in place.
    
    Voxel size is exposed as a writeable attribute, but is calculated
    on-the-fly from the header's ``cella`` and ``mx``/``my``/``mz`` fields.
    
    Three-dimensional data can represent either a stack of 2D images, or a 3D
    volume. This is indicated by the header's ``ispg`` (space group) field,
    which is set to 0 for image data and >= 1 for volume data. The
    :meth:`is_single_image`, :meth:`is_image_stack`, :meth:`is_volume` and
    :meth:`is_volume_stack` methods can be used to identify the type of
    information stored in the data array. For 3D data, the
    :meth:`set_image_stack` and :meth:`set_volume` methods can be used to
    switch between image stack and volume interpretations of the data.
    
    If the data contents have been changed, you can use the
    :meth:`update_header_from_data` and :meth:`update_header_stats` methods to
    make the header consistent with the data. These methods are called
    automatically if the data array is replaced by calling :meth:`set_data`.
    :meth:`update_header_from_data` is fast, even with very large data arrays,
    because it only examines the shape and type of the data array.
    :meth:`update_header_stats` calculates statistics from all items in the
    data array and so can be slow for very large arrays. If necessary, the
    :meth:`reset_header_stats` method can be called to set the header fields to
    indicate that the statistics are undetermined.
    
    Attributes:
    
    * :attr:`header`
    * :attr:`extended_header`
    * :attr:`data`
    * :attr:`voxel_size`
    
    Methods:
    
    * :meth:`set_extended_header`
    * :meth:`set_data`
    * :meth:`is_single_image`
    * :meth:`is_image_stack`
    * :meth:`is_volume`
    * :meth:`is_volume_stack`
    * :meth:`set_image_stack`
    * :meth:`set_volume`
    * :meth:`update_header_from_data`
    * :meth:`update_header_stats`
    * :meth:`reset_header_stats`
    * :meth:`print_header`
    
    Attributes and methods relevant to subclasses:
    
    * :attr:`_read_only`
    * :meth:`_check_writeable`
    * :meth:`_create_default_attributes`
    * :meth:`_close_data`
    * :meth:`_set_new_data`
    
    """
    
    def __init__(self, **kwargs):
        """Initialise a new :class:`MrcObject`.
        
        This initialiser deliberately avoids creating any arrays and simply
        sets the header, extended header and data attributes to :data:`None`.
        This allows subclasses to call :meth:`super().__init__` at the start of
        their initialisers and then set the attributes themselves, probably by
        reading from a file, or by calling :meth:`_create_default_attributes`
        for a new empty object.
        
        Note that this behaviour might change in future: this initialiser could
        take optional arguments to allow the header and data to be provided
        by the caller, or might create the standard empty defaults rather than
        setting the attributes to :data:`None`.
        """
        super(MrcObject, self).__init__(**kwargs)
        
        # Set empty default attributes
        self._header = None
        self._extended_header = None
        self._data = None
        self._read_only = False
    
    def _check_writeable(self):
        """Check that this MRC object is writeable.
        
        Raises:
            :class:`~exceptions.ValueError`: If this object is read-only.
        """
        if self._read_only:
            raise ValueError('MRC object is read-only')
    
    def _create_default_attributes(self):
        """Set valid default values for the header and data attributes."""
        self._create_default_header()
        self._extended_header = np.fromstring('', dtype='V1')
        self._set_new_data(np.fromstring('', dtype=np.int8))
    
    def _create_default_header(self):
        """Create a default MRC file header.
        
        The header is initialised with standard file type and version
        information, default values for some essential fields, and zeros
        elsewhere. The first text label is also set to indicate the file was
        created by this module.
        """
        self._header = np.zeros(shape=(), dtype=HEADER_DTYPE).view(np.recarray)
        header = self._header
        header.map = MAP_ID
        header.nversion = MRC_FORMAT_VERSION
        header.machst = utils.machine_stamp_from_byte_order(header.mode.dtype.byteorder)
        
        # Default space group is P1
        header.ispg = VOLUME_SPACEGROUP
        
        # Standard cell angles all 90.0 degrees
        default_cell_angle = 90.0
        header.cellb.alpha = default_cell_angle
        header.cellb.beta = default_cell_angle
        header.cellb.gamma = default_cell_angle
        # (this can also be achieved by assigning a 3-tuple to header.cellb
        # directly but using the sub-fields individually is easier to read and
        # understand)
        
        # Standard axes: columns = X, rows = Y, sections = Z
        header.mapc = 1
        header.mapr = 2
        header.maps = 3
        
        time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        header.label[0] = '{0:40s}{1:>40s}'.format('Created by mrcfile.py',
                                                   time)
        header.nlabl = 1
        
        self.reset_header_stats()
    
    @property
    def header(self):
        """Get the header as a :class:`numpy record array <numpy.recarray>`."""
        return self._header
    
    @property
    def extended_header(self):
        """Get the extended header as a :class:`numpy array <numpy.ndarray>`.
        
        If this :class:`MrcObject` was read from a file and the extended header
        type was recognised, its dtype will be set appropriately. (Currently
        the only supported type is ``'FEI1'``.) Otherwise, the dtype will be
        void (raw data, dtype ``'V'``). If the actual data type of the extended
        header is known, the dtype of the array can be changed to match.
        
        The extended header may be modified in place. To replace it completely,
        call :meth:`set_extended_header`.
        """
        return self._extended_header
    
    def set_extended_header(self, extended_header):
        """Replace the extended header.
        
        If you set the extended header you should also set the
        ``header.exttyp`` field to indicate the type of extended header.
        """
        self._check_writeable()
        self._extended_header = extended_header
        self.header.nsymbt = extended_header.nbytes
    
    @property
    def data(self):
        """Get the data as a :class:`numpy array <numpy.ndarray>`."""
        return self._data
    
    def set_data(self, data):
        """Replace the data array.
        
        This replaces the current data with the given array (or a copy of it),
        and updates the header to match the new data dimensions. The data
        statistics (min, max, mean and rms) stored in the header will also be
        updated.
        """
        self._check_writeable()
        
        # Check if the new data's dtype is valid without changes
        mode = utils.mode_from_dtype(data.dtype)
        new_dtype = (utils.dtype_from_mode(mode)
                     .newbyteorder(data.dtype.byteorder))
        
        # Copy the data if necessary to ensure correct dtype and C ordering
        new_data = np.asanyarray(data, new_dtype, order='C')
        
        # Replace the old data array with the new one, and update the header
        self._close_data()
        self._set_new_data(new_data)
        self.update_header_from_data()
        self.update_header_stats()
    
    def _close_data(self):
        """Close the data array."""
        self._data = None
    
    def _set_new_data(self, data):
        """Replace the data array with a new one.
        
        The new data array is not checked - it must already be valid for use in
        an MRC file.
        """
        self._data = data
    
    @property
    def voxel_size(self):
        """Get or set the voxel size in angstroms.
        
        The voxel size is returned as a structured :class:`numpy record array
        <numpy.recarray>` with three fields (x, y and z). Note that changing
        the voxel_size array in-place will *not* change the voxel size in the
        file -- to prevent this being overlooked accidentally, the writeable
        on the voxel_size array.
        
        To set the voxel size, assign a new value to the voxel_size attribute.
        You may give a single number, a 3-tuple ``(x, y ,z)`` or a modified
        version of the voxel_size array. The following examples are all
        equivalent:
        
        >>> mrc.voxel_size = 1.0
        
        >>> mrc.voxel_size = (1.0, 1.0, 1.0)
        
        >>> vox_sizes = mrc.voxel_size
        >>> vox_sizes.flags.writeable = True
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
        self._check_writeable()
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
        """Set the voxel size.
        
        Args:
            x_size: The voxel size in the X direction, in angstroms
            y_size: The voxel size in the Y direction, in angstroms
            z_size: The voxel size in the Z direction, in angstroms
        """
        self.header.cella.x = x_size * self.header.mx
        self.header.cella.y = y_size * self.header.my
        self.header.cella.z = z_size * self.header.mz
    
    def is_single_image(self):
        """Identify whether the file represents a single image.
        
        Returns:
            :data:`True` if the data array is two-dimensional.
        """
        return self.data.ndim == 2
    
    def is_image_stack(self):
        """Identify whether the file represents a stack of images.
        
        Returns:
            :data:`True` if the data array is three-dimensional and the space group
            is zero.
        """
        return (self.data.ndim == 3
                and self.header.ispg == IMAGE_STACK_SPACEGROUP)
    
    def is_volume(self):
        """Identify whether the file represents a volume.
        
        Returns:
            :data:`True` if the data array is three-dimensional and the space
            group is not zero.
        """
        return (self.data.ndim == 3
                and self.header.ispg != IMAGE_STACK_SPACEGROUP)
    
    def is_volume_stack(self):
        """Identify whether the file represents a stack of volumes.
        
        Returns:
            :data:`True` if the data array is four-dimensional.
        """
        return self.data.ndim == 4
    
    def set_image_stack(self):
        """Change three-dimensional data to represent an image stack.
        
        This method changes the space group number (``header.ispg``) to zero.
        
        Raises:
            :class:`~exceptions.ValueError`: If the data array is not
                three-dimensional.
        """
        self._check_writeable()
        if self.data.ndim != 3:
            raise ValueError('Only 3D data can be changed into an image stack')
        self.header.ispg = IMAGE_STACK_SPACEGROUP
        self.header.mz = 1
    
    def set_volume(self):
        """Change three-dimensional data to represent a volume.
        
        If the space group was previously zero (representing an image stack),
        this method sets it to one. Otherwise the space group is not changed.
        
        Raises:
            :class:`~exceptions.ValueError`: If the data array is not
                three-dimensional.
        """
        self._check_writeable()
        if self.data.ndim != 3:
            raise ValueError('Only 3D data can be changed into a volume')
        if self.is_image_stack():
            self.header.ispg = VOLUME_SPACEGROUP
            self.header.mz = self.header.nz
    
    def update_header_from_data(self):
        """Update the header from the data array.
        
        This function updates the header byte order and machine stamp to match
        the byte order of the data. It also updates the file mode, space group
        and the dimension fields ``nx``, ``ny``, ``nz``, ``mx``, ``my`` and
        ``mz``.
        
        If the data is 2D, the space group is set to 0 (image stack). For 3D
        data the space group is not changed, and for 4D data the space group is
        set to 401 (simple P1 volume stack) unless it is already in the volume
        stack range (401--630).
        
        This means that new 3D data will be treated as an image stack if the
        previous data was a single image or image stack, or as a volume if the
        previous data was a volume or volume stack.
        
        Note that this function does *not* update the data statistics fields in
        the header (``dmin``, ``dmax``, ``dmean`` and ``rms``). Use the
        :meth:`update_header_stats` function to update the statistics.
        (This is for performance reasons -- updating the statistics can take a
        long time for large data sets, but updating the other header
        information is always fast because only the type and shape of the data
        array need to be inspected.)
        """
        self._check_writeable()
        
        # Check the dtype is one we can handle and update mode to match
        header = self.header
        header.mode = utils.mode_from_dtype(self.data.dtype)
        
        # Ensure header byte order and machine stamp match the data's byte order
        data_byte_order = self.data.dtype.byteorder
        header_byte_order = header.mode.dtype.byteorder
        if (data_byte_order != '|'
            and not utils.byte_orders_equal(data_byte_order, header_byte_order)):
            header.byteswap(True)
            header.dtype = header.dtype.newbyteorder(data_byte_order)
        header.machst = utils.machine_stamp_from_byte_order(header.mode.dtype
                                                            .byteorder)
        
        shape = self.data.shape
        axes = len(shape)
        if axes == 2:
            # Single image. Space group 0, nz = mz = 1
            header.ispg = IMAGE_STACK_SPACEGROUP
            header.nx = header.mx = shape[1]
            header.ny = header.my = shape[0]
            header.nz = header.mz = 1
        elif axes == 3:
            header.nx = header.mx = shape[2]
            header.ny = header.my = shape[1]
            if header.ispg == IMAGE_STACK_SPACEGROUP:
                # Image stack. mz = 1, nz = sections in the volume
                header.mz = 1
                header.nz = shape[0]
            else:
                # Volume. nz = mz = sections in the volume
                header.nz = header.mz = shape[0]
        elif axes == 4:
            # Volume stack. Space group 401, mz = secs per vol, nz = total sections
            if not utils.spacegroup_is_volume_stack(header.ispg):
                header.ispg = VOLUME_STACK_SPACEGROUP
            header.nx = header.mx = shape[3]
            header.ny = header.my = shape[2]
            header.mz = shape[1]
            header.nz = shape[0] * shape[1]
        else:
            raise ValueError('Data must be 2-, 3- or 4-dimensional')
    
    def update_header_stats(self):
        """Update the header's ``dmin``, ``dmax``, ``dmean`` and ``rms`` fields
        from the data.
        
        Note that this can take some time with large files, particularly with
        files larger than the currently available memory.
        """
        self._check_writeable()
        
        self.header.dmin = self.data.min()
        self.header.dmax = self.data.max()
        
        # Use a float64 accumulator to calculate mean and standard deviation
        # This prevents overflow errors during calculation
        self.header.dmean = np.float32(self.data.mean(dtype=np.float64))
        self.header.rms = np.float32(self.data.std(dtype=np.float64))
    
    def reset_header_stats(self):
        """Set the header statistics to indicate that the values are unknown."""
        self._check_writeable()
        
        self.header.dmin = 0
        self.header.dmax = -1
        self.header.dmean = -2
        self.header.rms = -1
    
    def print_header(self, print_file=None):
        """Print the contents of all header fields.
        
        Args:
            print_file: The output text stream to use for printing the header.
                This is passed directly to the ``file`` argument of Python's
                :func:`print` function. The default is :data:`None`, which
                means output will be printed to :data:`sys.stdout`.
        """
        for item in self.header.dtype.names:
            print('{0:15s} : {1}'.format(item, self.header[item]),
                  file=print_file)
    
    def validate(self, print_file=None):
        """Validate this MrcObject.
        
        This method runs a series of tests to check whether this object
        complies with the MRC2014 format specification:
        
        #. MRC format ID string: The header's ``map`` field must contain
           "MAP ".
        #. Machine stamp: The machine stamp should contain one of
           ``0x44 0x44 0x00 0x00``, ``0x44 0x41 0x00 0x00`` or
           ``0x11 0x11 0x00 0x00``.
        #. MRC mode: the ``mode`` field should be one of the supported mode
           numbers: 0, 1, 2, 4 or 6.
        #. Map and cell dimensions: The header fields ``nx``, ``ny``, ``nz``,
           ``mx``, ``my``, ``mz``, ``cella.x``, ``cella.y`` and ``cella.z``
           must all be positive numbers.
        #. Axis mapping: Header fields ``mapc``, ``mapr`` and ``maps`` must
           contain the values 1, 2, and 3 (in any order).
        #. Volume stack dimensions: If the spacegroup is in the range 401--630,
           representing a volume stack, the ``nz`` field should be exactly
           divisible by ``mz`` to represent the number of volumes in the stack.
        #. Header labels: The ``nlabl`` field should be set to indicate the
           number of labels in use, and the labels in use should appear first
           in the label array (that is, there should be no blank labels between
           text-filled ones).
        #. MRC format version: The ``nversion`` field should be 20140 for
           compliance with the MRC2014 standard.
        #. Extended header type: If an extended header is present, the
           ``exttyp`` field should be set to indicate the type of extended
           header.
        #. Data statistics: The statistics in the header should be correct for
           the actual data, or marked as undetermined.
        
        Args:
            print_file: The output text stream to use for printing messages
                about the validation. This is passed directly to the ``file``
                argument of Python's :func:`print` function. The default is
                :data:`None`, which means output will be printed to
                :data:`sys.stdout`.
        
        Returns:
            :data:`True` if this MrcObject  is valid, or :data:`False` if it
            does not meet the MRC format specification in any way.
        """
        valid = True
        
        def log(message):
            print(message, file=print_file)
        
        # Check map ID string
        if self.header.map != MAP_ID:
            log("Map ID string is incorrect: found {0}, should be {1}"
                .format(self.header.map, MAP_ID))
            valid = False
        
        # Check machine stamp
        try:
            utils.byte_order_from_machine_stamp(self.header.machst)
        except ValueError:
            pretty_bytes = " ".join("0x{:02x}".format(byte)
                                    for byte in self.header.machst)
            log("Invalid machine stamp: " + pretty_bytes)
            valid = False
        
        # Check mode is valid
        try:
            utils.dtype_from_mode(self.header.mode)
        except ValueError:
            log("Invalid mode: {0}".format(self.header.mode))
            valid = False
        
        # Check map dimensions and other fields are non-negative
        for field in ['nx', 'ny', 'nz', 'mx', 'my', 'mz', 'ispg', 'nlabl']:
            if self.header[field] < 0:
                log("Header field '{0}' is negative".format(field))
                valid = False
        
        # Check cell dimensions are non-negative
        for field in ['x', 'y', 'z']:
            if self.header.cella[field] < 0:
                log("Cell dimension '{0}' is negative".format(field))
                valid = False
        
        # Check axis mapping is valid
        axes = set()
        for field in ['mapc', 'mapr', 'maps']:
            axes.add(int(self.header[field]))
        if axes != set([1, 2, 3]):
            log("Invalid axis mapping: found {0}, should be [1, 2, 3]"
                .format(sorted(list(axes))))
            valid = False
        
        # Check mz value for volume stacks
        if utils.spacegroup_is_volume_stack(self.header.ispg):
            if self.header.nz % self.header.mz != 0:
                log("Error in dimensions for volume stack: nz should be "
                    "divisible by mz. Found nz = {0}, mz = {1})"
                    .format(self.header.nz, self.header.mz))
                valid = False
        
        # Check nlabl is correct
        count = 0
        seen_empty_label = False
        for label in self.header.label:
            if len(label.strip()) > 0:
                count += 1
                if seen_empty_label:
                    log("Error in header labels: empty labels appear between "
                        "text-containing labels")
                    valid = False
            else:
                seen_empty_label = True
        if count != self.header.nlabl:
            log("Error in header labels: nlabl is {0} "
                "but {1} labels contain text".format(self.header.nlabl, count))
            valid = False
        
        # Check MRC format version
        if self.header.nversion != MRC_FORMAT_VERSION:
            log("File does not declare MRC format version 20140: nversion = {0}"
                .format(self.header.nversion))
            valid = False
        
        # Check extended header type is set to a known value
        valid_exttypes = ['CCP4', 'MRCO', 'SERI', 'AGAR', 'FEI1']
        if self.header.nsymbt > 0 and self.header.exttyp not in valid_exttypes:
            log("Extended header type is undefined or unrecognised: exttyp = "
                "'{0}'".format(self.header.exttyp.item().decode('ascii')))
            valid = False
        
        # Check data statistics
        if self.data is not None:
            real_rms = real_min = real_max = real_mean = 0
            if len(self.data > 0):
                real_rms = self.data.std()
                real_min = self.data.min()
                real_max = self.data.max()
                real_mean = self.data.mean()
            if (self.header.rms >= 0 and not np.isclose(real_rms, self.header.rms)):
                log("Error in data statistics: RMS deviation is {0} but the value "
                    "in the header is {1}".format(real_rms, self.header.rms))
                valid = False
            if self.header.dmin < self.header.dmax and self.header.dmin != real_min:
                log("Error in data statistics: minimum is {0} but the value "
                    "in the header is {1}".format(real_min, self.header.dmin))
                valid = False
            if self.header.dmin < self.header.dmax and self.header.dmax != real_max:
                log("Error in data statistics: maximum is {0} but the value "
                    "in the header is {1}".format(real_max, self.header.dmax))
                valid = False
            if (self.header.dmean > min(self.header.dmin, self.header.dmax)
                and not np.isclose(real_mean, self.header.dmean)):
                log("Error in data statistics: mean is {0} but the value "
                    "in the header is {1}".format(real_mean, self.header.dmean))
                valid = False
        
        return valid
