from itertools import chain

import AST


# -------------------- Exceptions ----------------------  #

class RailwayException(RuntimeError):
    def __init__(self, message, scope):
        self.message = message
        self.stack = []
        while scope is not None:
            self.stack.append(scope.name)
            scope = scope.parent


class RailwayLeakedInformation(RailwayException): pass
class RailwayUndefinedVariable(RailwayException): pass
class RailwayVariableExists(RailwayException): pass
class RailwayIndexError(RailwayException): pass
class RailwayTypeError(RailwayException): pass
class RailwayUndefinedFunction(RailwayException): pass
class RailwayFailedAssertion(RailwayException): pass
class RailwayDirectionChange(RailwayException): pass


# -------------------- Interpreter-Only Objects ---------------------- #

class Scope:
    __slots__ = ['parent', 'name', 'locals', 'monos', 'globals']

    def __init__(self, parent, name, locals, monos, globals):
        self.parent = parent
        self.name = name
        self.locals = locals if locals is not None else {}
        self.monos = monos if monos is not None else {}
        self.globals = globals if globals is not None else {}

    def lookup(self, name, locals=True, globals=True, monos=True):
        if monos and name in self.monos:
            return self.monos[name]
        if locals and name in self.locals:
            return self.locals[name]
        if globals and name in self.globals:
            return self.globals[name]
        raise RailwayUndefinedVariable(
            f'Variable {name} is undefined',
            scope=self)

    def assign(self, name, var):
        namespace = self.monos if var.ismono else self.locals
        if name in namespace:
            raise RailwayVariableExists(
                f'Variable {name} already exists',
                scope=self)
        namespace[name] = var

    def remove(self, name):
        if name in self.monos:
            del self.monos[name]
        elif name in self.locals:
            del self.locals[name]
        else:
            raise RailwayUndefinedVariable(
                f'Local variable "{name}" does not exist',
                scope=self)


class Variable:
    __slots__ = ['memory', 'ismono', 'isborrowed', 'isarray']

    def __init__(self, memory, ismono=False, isborrowed=False, isarray=False):
        self.memory = memory
        self.ismono = ismono
        self.isborrowed = isborrowed
        self.isarray = isarray


class VariableMutator:
    __slots__ = ['memory', 'index']

    def __init__(self, memory, index):
        self.memory = memory
        self.index = index

    def get(self):
        return self.memory[self.index]

    def set(self, value):
        self.memory[self.index] = value


# -------------------- AST - Top Level Objects --------------------#

class Module(AST.Module):
    def eval(self):
        scope = Scope(parent=None, name='main',
                      locals={}, monos={}, globals={})
        if 'main' not in self.functions and '~main' not in self.functions:
            raise RailwayUndefinedFunction(
                f'There is no main function in {self.name}')
        main = self.functions.get('main', self.functions.get('~main', None))
        try:
            main.eval(scope, backwards=False)
        except RailwayException as e:
            print("\nError Call Stack:")
            for frame in e.stack:
                print('->', frame)
            print(type(e).__name__, ':', e.message)


class Function(AST.Function):
    def eval(self, scope, backwards):
        lines = reversed(self.lines) if backwards else self.lines
        for line in lines:
            line.eval(scope, backwards=backwards)
        if backwards:
            param_names = set(p.name for p in self.parameters)
            existing_names = set(scope.locals).union(set(scope.monos))
            missing = param_names.difference(existing_names)
            leaked = existing_names.difference(param_names)
            if missing:
                raise RailwayUndefinedVariable(
                    f'Parameter "{missing.pop()}" is not in scope of function '
                    f'{self.name} at the end of an uncall',
                    scope=scope)
            if leaked:
                raise RailwayLeakedInformation(
                        f'Variable "{leaked.pop()}" is still in scope of '
                        f'function {self.name} at the end of an uncall',
                        scope=scope)
            return_names = [p.name for p in self.parameters if not p.isborrowed]
        else:
            for name, var in chain(scope.locals.items(), scope.monos.items()):
                if not var.isborrowed and name != self.retname:
                    RailwayLeakedInformation(
                        f'Variable "{name}" is still in scope of function '
                        f'{self.name} at the end of a call',
                        scope=scope)
            return_names = [] if self.retname is None else [self.retname]
            return [scope.lookup(nm, globals=False) for nm in return_names]


# -------------------- AST - Print --------------------#

class Print(AST.Print):
    def eval(self, scope, backwards=False):
        # if backwards:
        #     return
        if isinstance(self.target, str):
            print(self.target)
        else:
            memory = self.target.eval(scope)
            print(self.stringify(memory))

    def stringify(self, memory):
        # Temporary implementation
        if isinstance(memory, Fraction):
            return str(memory)
        return '[' + ', '.join(self.stringify(elt) for elt in memory) + ']'


# -------------------- AST - Do-Yield-Undo --------------------#

class DoUndo(AST.DoUndo):
    def eval(self, scope, backwards):
        if scope.monos:
            name = next(iter(scope.monos.keys()))
            raise RailwayDirectionChange(
                'Changing direction of time using DO-YIELD-UNDO '
                f'whilst mono-directional variable "{name}" is in scope',
                scope=scope)
        for line in self.do_lines:
            line.eval(scope, backwards=False)
        yield_lines = self.yield_lines
        for line in reversed(yield_lines) if backwards else yield_lines:
            line.eval(scope, backwards)
        for line in reversed(self.do_lines):
            line.eval(scope, backwards=True)


# -------------------- AST - Loop, If --------------------#

class Loop(AST.Loop):
    def eval(self, scope, backwards):
        if backwards:
            condition = self.backward_condition
            assertion = self.forward_condition
            lines = self.lines[::-1]  # reversed builtin no good here
        else:
            condition = self.forward_condition
            assertion = self.backward_condition
            lines = self.lines
        if assertion.eval(scope):
            raise RailwayFailedAssertion(
                'Loop reverse condition is true before loop start',
                scope=scope)
        while condition.eval(scope):
            for line in lines:
                line.eval(scope, backwards)
            if not assertion.eval(scope):
                raise RailwayFailedAssertion(
                    'Foward loop condition holds when'
                    ' reverse condition does not',
                    scope=scope)


class If(AST.If):
    def eval(self, scope, backwards):
        enter_expr = self.exit_expr if backwards else self.enter_expr
        exit_expr = self.enter_expr if backwards else self.exit_expr
        enter_result = bool(enter_expr.eval(scope))
        lines = self.lines if enter_result else self.else_lines
        for line in reversed(lines) if backwards else lines:
            line.eval(scope, backwards)
        exit_result = bool(exit_expr.eval(scope))
        if exit_result != enter_result:
            raise RailwayFailedAssertion(
                'Failed exit assertion in if-fi statement',
                scope=scope)


# -------------------- AST - Modifications --------------------#

class Modop(AST.Modop):
    def eval(self, scope, backwards):
        op = self.inv_op if backwards else self.op
        lhs, rhs = self.lookup.eval(scope), self.expr.eval(scope)
        result = Fraction(op(lhs, rhs))
        self.lookup.set(scope, result)
        # The seperate lookup.eval and lookup.set calls are
        # an opportunity for optimisation


# -------------------- AST - Let and Unlet --------------------#

class Let(AST.Let):
    def eval(self, scope, backwards):
        if backwards:
            return unlet_eval(self, scope)
        return let_eval(self, scope)


class Unlet(AST.Unlet):
    def eval(self, scope, backwards):
        if backwards:
            return let_eval(self, scope)
        return unlet_eval(self, scope)


def let_eval(self, scope):
    lhs, rhs = self.lookup, self.rhs
    isarray = bool(lhs.index)
    value = rhs.eval(scope=scope) if rhs is not None else Fraction(0)
    if isarray:
        lengths = [int(expr.eval(scope=scope)) for expr in lhs.index]
    else:
        lengths = [1]
        if not isinstance(value, Fraction):
            raise RailwayIndexError(
                f'Cannot Let number "{lhs.name}" with array', scope=scope)
    try:
        memory = create_memory(lengths, value)
    except TypeError:
        raise RailwayIndexError('Encountered array when initialising '
                                f'a numerical element of in "{lhs.name}"',
                                scope=scope)
    except IndexError:
        raise RailwayIndexError(
            f'Initilising "{lhs.name}" with an array of insufficient dimension',
            scope=scope)
    var = Variable(memory=memory, ismono=lhs.ismono, isarray=isarray)
    scope.assign(name=lhs.name, var=var)


def unlet_eval(self, scope):
    lhs, rhs = self.lookup, self.rhs
    var = scope.lookup(name=lhs.name, globals=False)
    if var.isarray != bool(lhs.index):
        raise RailwayTypeError(
            f'Variable "{lhs.name}" is {"" if var.isarray else "not"} an '
            f'array but Unlet has {"no" if var.isarray else""} indices',
            scope=scope)
    try:
        compare_memory(
            var.memory if var.isarray else var.memory[0],
            rhs.eval(scope=scope) if rhs is not None else Fraction(0))
    except IndexError:
        raise RailwayIndexError(f'Unletting variable "{lhs.name}" '
                                'using expression of incorrect shape',
                                scope=scope)
    except ValueError:
        raise RailwayIndexError(f'Value mismatch during Unlet of "{lhs.name}"',
                                scope=scope)
    scope.remove(lhs.name)


def create_memory(length_list, vals, depth=0):
    if depth < len(length_list):
        return [create_memory(length_list,
                              vals[i] if isinstance(vals, list) else vals,
                              depth+1)
                for i in range(length_list[depth])]
    if not isinstance(vals, Fraction):
        raise TypeError("Raiway control flow: create memory")
    return vals


def compare_memory(memory, vals):
    if isinstance(memory, list):
        if isinstance(vals, Fraction):
            for mem in memory:
                compare_memory(mem, vals)
        elif len(memory) != len(vals):
            raise IndexError("Railway control flow: compare_memory")
        else:
            for mem, val in zip(memory, vals):
                compare_memory(mem, val)
    else:  # Otherwise fraction
        if not isinstance(vals, Fraction):
            raise IndexError("Railway control flow: compare_memory")
        if vals != memory:
            raise ValueError("Railway control flow: compare_memory")


# -------------------- Expressions -------------------- #

class Binop(AST.Binop):
    def eval(self, scope):
        lhs = self.lhs.eval(scope=scope)
        rhs = self.rhs.eval(scope=scope)
        if isinstance(lhs, list) or isinstance(rhs, list):
            raise RailwayTypeError(
                f'Binary operation {self.name} does not accept arrays',
                scope=scope)
        return Fraction(self.op(lhs, rhs))


class Uniop(AST.Uniop):
    def eval(self, scope):
        val = self.expr.eval(scope=scope)
        if isinstance(val, list):
            raise RailwayTypeError(
                f'Unary operation {self.name} does not accept arrays',
                scope=scope)
        return Fraction(self.op(val))


class Length(AST.Length):
    def eval(self, scope):
        var = scope.lookup(self.lookup.name)
        if not var.isarray:
            raise RailwayTypeError(f'Variable "{self.lookup.name}" '
                                   'has no length as it is not an array',
                                   scope=scope)
        return Fraction(len(var.memory))


class Lookup(AST.Lookup):
    def eval(self, scope):
        var = scope.lookup(self.name)
        if var.isarray:  # Arrays
            index = [int(idx.eval(scope=scope)) for idx in self.index]
            output = var.memory
            try:
                for idx in index:
                    output = output[idx]
            except IndexError:
                index_repr = f'{self.name}[{", ".join(str(i) for i in index)}]'
                if isinstance(output, Fraction):
                    msg = 'Indexing into number during lookup '
                else:
                    msg = 'Out of bounds error accessing '
                raise RailwayIndexError(msg + index_repr, scope=scope)
        else:  # Non-arrays (numbers)
            if self.index:
                raise RailwayIndexError(
                    f'Indexing into {self.name} which is a number',
                    scope=scope)
            output = var.memory[0]
        return output

    def set(self, scope, value):
        var = scope.lookup(self.name)
        memory = var.memory
        if var.isarray:
            indices = [int(idx.eval(scope=scope)) for idx in self.index]
            lookup_str = f'{self.name}[{",".join(str(i) for i in indices)}]'
            try:
                for idx in indices[:-1]:
                    memory = memory[idx]
                index = indices[-1]
                if not isinstance(memory[index], Fraction):
                    raise RailwayTypeError(
                        f'Trying to modify array "{lookup_str}" with a number',
                        scope=scope)
            except (IndexError, TypeError) as e:
                if isinstance(memory, Fraction) or isinstance(e, TypeError):
                    msg = 'Indexing into number during lookup '
                else:
                    msg = 'Out of bounds error accessing '
                raise RailwayIndexError(msg + lookup_str, scope=scope)
        else:  # Non-array (so a number (so a special array of length 1))
            if self.index:
                raise RailwayIndexError(
                    f'Indexing into {self.name} which is a number',
                    scope=scope)
            index = 0
        memory[index] = value


class Fraction(AST.Fraction):
    def eval(self, scope=None):
        return self


# Parameters are never eval'd to don't need extending
Parameter = AST.Parameter


binops = {'ADD': lambda a, b: a + b,
          'SUB': lambda a, b: a - b,
          'MUL': lambda a, b: a * b,
          'DIV': lambda a, b: a / b,
          'POW': lambda a, b: a ** b,
          'IDIV': lambda a, b: a // b,
          'MOD': lambda a, b: a % b,
          'XOR': lambda a, b: bool(a) ^ bool(b),
          'OR': lambda a, b: bool(a) | bool(b),
          'AND': lambda a, b: bool(a) & bool(b),
          'LESS': lambda a, b: a < b,
          'LEQ': lambda a, b: a <= b,
          'GREAT': lambda a, b: a > b,
          'GEQ': lambda a, b: a >= b,
          'EQ': lambda a, b: a == b,
          'NEQ': lambda a, b: a != b}

uniops = {'NOT': lambda x: not bool(x),
          'SUB': lambda x: -x}

modops = {'MODADD': lambda a, b: a + b,
          'MODSUB': lambda a, b: a - b,
          'MODMUL': lambda a, b: a * b,
          'MODDIV': lambda a, b: a / b}

inv_modops = {'MODADD': modops['MODSUB'],
              'MODSUB': modops['MODADD'],
              'MODMUL': modops['MODDIV'],
              'MODDIV': modops['MODMUL']}