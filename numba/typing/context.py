from __future__ import print_function
from collections import defaultdict
from numba import types, utils
from . import templates


def _type_distance(domain, first, second):
    if first in domain and second in domain:
        return domain.index(first) - domain.index(second)


class Context(object):
    """A typing context for storing function typing constrain template.
    """
    def __init__(self):
        self.type_lattice = types.type_lattice
        self.functions = defaultdict(list)
        self.attributes = {}
        self.globals = utils.UniqueDict()
        self._load_builtins()

    def get_number_type(self, num):
        if isinstance(num, int):
            nbits = utils.bit_length(num)
            if nbits < 32:
                typ = types.int32
            elif nbits < 64:
                typ = types.int64
            else:
                raise ValueError("Int value is too large: %s" % num)
            return typ
        elif isinstance(num, float):
            return types.float64
        else:
            raise NotImplementedError(type(num), num)

    def resolve_function_type(self, func, args, kws):
        if isinstance(func, types.Function):
            return func.template(self).apply(args, kws)

        defns = self.functions[func]
        for defn in defns:
            res = defn.apply(args, kws)
            if res is not None:
                return res

    def resolve_getattr(self, value, attr):
        try:
            attrinfo = self.attributes[value]
        except KeyError:
            if value.is_parametric:
                attrinfo = self.attributes[type(value)]
            else:
                raise

        return attrinfo.resolve(value, attr)

    def resolve_setitem(self, target, index, value):
        args = target, index, value
        kws = ()
        return self.resolve_function_type("setitem", args, kws)

    def get_global_type(self, gv):
        return self.globals[gv]

    def _load_builtins(self):
        for ftcls in templates.BUILTINS:
            self.insert_function(ftcls(self))
        for ftcls in templates.BUILTIN_ATTRS:
            self.insert_attributes(ftcls(self))
        for gv, gty in templates.BUILTIN_GLOBALS:
            self.insert_global(gv, gty)

    def insert_global(self, gv, gty):
        self.globals[gv] = gty

    def insert_attributes(self, at):
        key = at.key
        assert key not in self.functions, "Duplicated attributes template"
        self.attributes[key] = at

    def insert_function(self, ft):
        key = ft.key
        self.functions[key].append(ft)

    def insert_user_function(self, fn, ft):
        self.insert_function(ft)
        self.globals[fn] = types.Function(ft)

    def insert_class(self, cls, attrs):
        clsty = types.Object(cls)
        at = templates.ClassAttrTemplate(self, clsty, attrs)
        self.insert_attributes(at)

    def type_distance(self, fromty, toty):
        if fromty == toty:
            return 0
        elif (isinstance(fromty, types.UniTuple) and
                  isinstance(toty, types.UniTuple) and
                  len(fromty) == len(toty)):
            return self.type_lattice.get((fromty.dtype, toty.dtype))
        return self.type_lattice.get((fromty, toty))

    def unify_types(self, *types):
        return reduce(self.unify_pairs, types)

    def unify_pairs(self, first, second):
        """
        Choose PyObject type as the abstract if we fail to determine a concrete
        type.
        """
        # TODO: should add an option to reject unsafe type conversion
        d = self.type_distance(fromty=first, toty=second)
        if d is None:
            return types.pyobject
        elif d >= 0:
            # A promotion from first -> second
            return second
        else:
            # A demotion from first -> second
            return first


def new_method(fn, sig):
    gvs = dict(key=fn, cases=[sig])
    ft = type("UserFunction_%s" % fn, (templates.ConcreteTemplate,), gvs)
    return types.Method(ft, this=sig.recvr)