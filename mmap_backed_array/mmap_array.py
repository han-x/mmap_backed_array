"""mmap backed array datastructure"""
import mmap as _mmap
import os

from cffi import FFI
ffi = FFI()
ffi.cdef("""
typedef unsigned int mode_t;
int shm_open(const char *name, int oflag, mode_t mode);
int shm_unlink(const char *name);
""")
C = ffi.verify("""
#include <sys/mman.h>
""", libraries=["rt"])


_typecode_to_type = {
    'c': ffi.typeof('char'),         'u': ffi.typeof('wchar_t'),
    'b': ffi.typeof('signed char'),  'B': ffi.typeof('unsigned char'),
    'h': ffi.typeof('signed short'), 'H': ffi.typeof('unsigned short'),
    'i': ffi.typeof('signed int'),   'I': ffi.typeof('unsigned int'),
    'l': ffi.typeof('signed long'),  'L': ffi.typeof('unsigned long'),
    'f': ffi.typeof('float'),        'd': ffi.typeof('double'),
}

__all__ = [
    "mmaparray",
]

def anon_mmap(data):
    data = memoryview(data)
    size = len(data)
    name_str = '/{}'.format(os.getpid())
    name = bytes(name_str, 'ascii')
    fd = C.shm_open(name, os.O_RDWR|os.O_CREAT|os.O_EXCL, 0o600)
    if fd < 0:
        errno = ffi.errno
        raise OSError(errno, os.seterror(errno))
    try:
        if C.shm_unlink(name) != 0:
            errno = ffi.errno
            raise OSError(errno, os.seterror(errno))
        os.write(fd, data)
        result = _mmap.mmap(fd, size)
    finally:
        os.close(fd)
    return result


import ctypes
def address_of_buffer(buf):
    """Find the address of a buffer"""
    return ctypes.addressof(ctypes.c_char.from_buffer(buf))

class mmaparray:
    """mmap backed Array like data structure"""
    def __new__(cls, typecode, *args, **kwargs):
        """:typecode: the typecode for the underlying mmap array"""
        self = object.__new__(cls)

        # Validate the typecode provided
        if not isinstance(typecode, str) or len(typecode) != 1:
            raise TypeError
        try:
            itemtype = _typecode_to_type[typecode]
        except KeyError:
            raise ValueError
        self._itemtype = itemtype
        self._typecode = typecode
        self._ptrtype = ffi.typeof( ffi.getctype(itemtype, '*') )
        self._itemsize = ffi.sizeof(itemtype)

        # validate *args
        if len(args) > 1:
            raise TypeError("expected 1 or 2 arguments, got %d" % (1+len(args)))
        if len(args) == 1:
            data = args[0]
            iter(data) # verify that args is iterable
        else:
            data = None

        # validate **kwargs
        mmap = kwargs.pop('mmap', None)
        if kwargs:
            raise TypeError("unexpected keyword arguments %r" % kwargs.keys())

        # handle default mmap, validate and store mmap, compute size
        if mmap is None:
            raise NotImplementedError("TODO: create default mmap")
        elif not isinstance(mmap, _mmap.mmap):
            raise TypeError("expected an mmap instance, got %r" % mmap)
        else:
            size = len(mmap)
            size -= size % self.itemsize
        self._mmap = mmap

    def __len__(self):
        return self._length

