from __future__ import print_function
import llvm.core as lc
import llvm.passes as lp
import llvm.ee as le
from numba import _dynfunc, config
from numba.callwrapper import PyCallWrapper
from .base import BaseContext
from numba import utils


class CPUContext(BaseContext):
    def init(self):
        self.execmodule = lc.Module.new("numba.exec")
        eb = le.EngineBuilder.new(self.execmodule).opt(3)
        self.tm = tm = eb.select_target()
        self.engine = eb.create(tm)

        pms = lp.build_pass_managers(tm=self.tm, loop_vectorize=True,
                                     opt=3, fpm=False)
        self.pm = pms.pm

        self.native_funcs = utils.UniqueDict()
        # self.pm = lp.PassManager.new()
        # self.pm.add(lp.Pass.new("mem2reg"))
        # self.pm.add(lp.Pass.new("simplifycfg"))

    def dynamic_map_function(self, func):
        name, ptr = self.native_funcs[func]
        le.dylib_add_symbol(name, ptr)

    def optimize(self, module):
        self.pm.run(module)

    def get_executable(self, func, fndesc):
        if not fndesc.native:
            self.optimize_pythonapi(func)

        wrapper, api = PyCallWrapper(self, func.module, func, fndesc).build()
        moddictsym = api.get_module_dict_symbol()
        self.optimize(func.module)

        if config.DEBUG:
            print(func.module)

        self.engine.add_module(func.module)
        baseptr = self.engine.get_pointer_to_function(func)
        fnptr = self.engine.get_pointer_to_function(wrapper)
        moddictptr = self.engine.get_pointer_to_global(moddictsym)
        cfunc = _dynfunc.make_function(fndesc.pymod, fndesc.name, fndesc.doc,
                                       fnptr)

        _dynfunc.set_arbitrary_addr(moddictptr, fndesc.pymod.__dict__)
        self.native_funcs[cfunc] = fndesc.name, baseptr
        return cfunc, fnptr

    def optimize_pythonapi(self, func):
        # Simplify the function using
        pms = lp.build_pass_managers(tm=self.tm, opt=1,
                                     mod=func.module)
        fpm = pms.fpm

        fpm.initialize()
        fpm.run(func)
        fpm.finalize()

        # remove extra refct api calls
        remove_refct_calls(func)


def remove_refct_calls(func):
    """
    Remove redundant incref/decref within on a per block basis
    """
    for bb in func.basic_blocks:
        remove_null_refct_call(bb)
        remove_refct_pairs(bb)


def remove_null_refct_call(bb):
    """
    Remove refct api calls to NULL pointer
    """
    for inst in bb.instructions:
        if isinstance(inst, lc.CallOrInvokeInstruction):
            fname = inst.called_function.name
            if fname == "Py_IncRef" or fname == "Py_DecRef":
                arg = inst.operands[0]
                if isinstance(arg, lc.ConstantPointerNull):
                    inst.erase_from_parent()


def remove_refct_pairs(bb):
    """
    Remove incref decref pairs on the same variable
    """

    didsomething = True

    while didsomething:
        didsomething = False

        increfs = {}
        decrefs = {}

        # Mark
        for inst in bb.instructions:
            if isinstance(inst, lc.CallOrInvokeInstruction):
                fname = inst.called_function.name
                if fname == "Py_IncRef":
                    arg = inst.operands[0]
                    increfs[arg] = inst
                elif fname == "Py_DecRef":
                    arg = inst.operands[0]
                    decrefs[arg] = inst

        # Sweep
        for val in increfs.keys():
            if val in decrefs:
                increfs[val].erase_from_parent()
                decrefs[val].erase_from_parent()
                didsomething = True