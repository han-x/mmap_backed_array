"""mmap backed array datastructure"""
import mmap as _mmap
import array, os, operator

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
    size = len(data)#TODO: check that this is actually correct, might need to be data.nbytes given this is a memoryview
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
            mmap = anon_mmap(b'\x00')
            size = 0
        elif not isinstance(mmap, _mmap.mmap):
            raise TypeError("expected an mmap instance, got %r" % mmap)
        else:
            size = len(mmap)
            size -= size % self.itemsize
        self._mmap = mmap
        self._setsize(size)

        #append the data
        if data is not None:
            if isinstance(data, bytes):
                self._frombytes(data)
            elif isinstance(data, str):
                self._fromstring(data)
            elif isinstance(data, array.array):
                if data.typecode == typecode:
                    self._frombytes(memoryview(data))
                else:
                    raise TypeError("Typecodes must be the same")
            elif isinstance(data, mmaparray):
                if data.typecode == typecode:
                    self._from_mmaparray(data)
                else:
                    raise TypeError("Typecodes must be the same")
            else:
                data = array.array(typecode, data)
                self._frombytes(memoryview(data))

        return self

    def _setsize(self, size):
        """Set the size of the mmap object"""
        self._size = size
        self._length = size//self._itemsize
        pointer_to_beginning_of_mmap_buffer = address_of_buffer(self._mmap)#TODO: can't use address of buffer from _multiprocessing
        self._data = ffi.cast(self._ptrtype, pointer_to_beginning_of_mmap_buffer)

    def _resize(self, size):
        """Resize the mmap object
        :size: new size
        """
        assert size >= 0
        if size == 0:
            self._mmap.resize(1)
            #self._mmap[0] = b'\x00' #This gives a typeerror in cpython 3.4
            self._mmap[0] = 0
        else:
            self._mmap.resize(size)
        self._setsize(size)


    #Array API
    def __eq__(self, other):
        try:
            return (self is other or
                    all(x==y for x,y in zip(self, other))
                    )
        except TypeError:
            return NotImplemented

    def __ge__(self, other):
        if self is not other:
            for x, y in zip(self, other):
                if x == y:
                    continue
                return x > y
        return True

    def __getitem__(self, index):
        if isinstance(index, int):
            if index < 0:
                index += self._length
                if index < 0:
                    raise IndexError
            elif index >= self._length:
                raise IndexError
            return self._data[index]
        raise NotImplementedError() #TODO: implement slices

    def __gt__(self, other):
        for x, y in zip(self, other):
            if x == y:
                continue
            return x > y
        return False

    def __le__(self, other):
        if self is not other:
            for x, y in zip(self, other):
                if x == y:
                    continue
                return x < y
        return True

    def __len__(self):
        return self._length

    def __lt__(self, other):
        for x, y in zip(self, other):
            if x == y:
                continue
            return x < y
        return False

    def __repr__(self):
        data = array.array(self.typecode, self._tobytes())
        return repr(data)

    def __setitem__(self, index, value):
        if isinstance(index, int):
            if index < 0:
                index += self._length
                if index < 0:
                    raise IndexError
            elif not index < self._length:
                raise IndexError
            self._data[index] = value
            return
        raise NotImplementedError() #TODO: implement slices

    def _frombytes(self, data):
        """Fill the mmap array from a bytes datasource
        :data: the data
        """
        if isinstance(data, memoryview):
            bytesize = data.nbytes
        else:
            bytesize = len(data)
        if bytesize%self.itemsize != 0:
            raise ValueError
        if bytesize:
            pos = self._size
            assert pos % self.itemsize == 0
            self._resize(pos+bytesize)
            if isinstance(data, bytes):
                ffi.cast("char*", self._data)[pos:pos+bytesize] = data
            else:
                ffi.cast("char*", self._data)[pos:pos+bytesize] = ffi.from_buffer(data)

    frombytes = _frombytes

    def _fromstring(self, data):
        if not isinstance(data, str):
            raise TypeError
        if self.typecode != 'u':
            raise ValueError
        bytes_encoded = data.encode('utf-32le') #Do we need to check that ffi.sizeof('wchar_t') == 4 first?
        self._frombytes(bytes_encoded)
    fromstring = _fromstring

    def _from_mmaparray(self, data):
        """Fill the mmap array from another mmap array object
        :data: mmaparray object
        """
        if not isinstance(data, mmaparray):
            raise TypeError
        if self.typecode != data.typecode:
            raise TypeError("Typecodes must be the same, got %s and %s" % (self.typecode, data.typecode))
        othersize = data._size
        if othersize%self.itemsize != 0:
            raise ValueError
        if othersize:
            pos = self._size
            self._resize(pos + othersize)
            #TODO: Check that this gets the underlying memory from the other mmaparray object correctly
            ffi.memmove(self._data + pos, data._data, othersize)


    def append(self, x):
        """Append an item to the Array
        :x: the item to append
        """
        pos = self._size
        assert pos % self.itemsize == 0
        try:
            self._resize(pos+self.itemsize)
            self._data[pos//self.itemsize] = x
        except TypeError:
            self._resize(pos)
            raise

    def count(self, x):
        return sum(x==y for y in self)

    def extend(self, items):
        raise NotImplementedError()

    def _fromfile(self, f, n):
        """Read in data from a file
        :f: the file
        :n: size to read
        """
        readsize = n*self.itemsize
        data = f.read(readsize)
        datasize = len(data)
        if datasize < readsize:
            datasize -= datasize%self.itemsize
            if datasize:
                self._frombytes(data[:datasize])
            raise EOFError #didn't get the whole file, need to try again?
        self._frombytes(data)
    fromfile = _fromfile


    def fromlist(self, items):
        data = array.array(self.typecode)
        data.fromlist(items)
        if data:
            self._frombytes(memoryview(data))
    _fromlist = fromlist

    def index(self, x):
        """Return the smallest i such that i is the index of the first occurrence of x in the array."""
        for i, y in enumerate(self):
            if y == x:
                return i
        raise ValueError

    def pop(self, i=-1):
        """Remove the item with the index i and return it.
        Optional argument defaults to -1 which will remove the last element."""
        raise NotImplementedError()

    def remove(self, x):
        """Remove the first occurence of x from the array"""
        self.pop(self.index(x))

    def reverse(self):
        """Reverse the order of the items in the array."""
        stop = self._length
        end = stop-1
        for i in range(stop//2):
            j = end-i
            tmp = self._data[i]
            self._data[i] = self._data[j]
            self._data[j] = tmp

    def tolist(self):
        return list(self)
    _tolist = tolist

    def tostring(self):
        return self.tobytes()#make this an alias for tobytes like cpython >3.2 ?
    _tostring = tostring

    def tobytes(self):
        return bytes(ffi.buffer(self._data, self._length * self._itemsize))
    _tobytes = tobytes

    itemsize = property(operator.attrgetter('_itemsize'))
    typecode = property(operator.attrgetter('_typecode'))
