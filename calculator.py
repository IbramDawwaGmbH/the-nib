"""A safe, local answer to a plain sum or a unit conversion, typed into the ask bubble.

Deliberately Qt-free, like :mod:`world` and :mod:`mood`, so it can be tested headlessly and reasoned
about on its own - it has no idea the bubble exists, only that it is handed a line of text and asked
whether it means something arithmetic.

**There is no `eval()` anywhere in this file, and there never should be.** ``evaluate()`` parses text
with :mod:`ast` and walks the tree itself, admitting only a short whitelist of node types, operators
and function names; anything else - a name that isn't ``pi`` or ``e``, an attribute lookup, a
comprehension, a lambda, an import - is refused, not attempted. The same walker also bounds the
*size* of what it will compute (an exponent, a bit length) rather than trusting Python's own limits,
because "safe from arbitrary code" and "safe from a pathological expression hanging the UI thread"
are two different guarantees and this module makes both.

Unit conversion is a second, independent grammar: ``<number> <unit> (to|in|as) <unit>``. A match
only proceeds once *both* sides name a real, known unit - one recognised unit and one ordinary word
is far more likely to be a sentence ("10 minutes in advance") than a mistake ("10 miles in kg"), and
this module would rather stay quiet on the former than be clever about the latter. Currency is not
here on purpose: every other conversion below is a fixed ratio between two physical quantities, true
forever, and free to bundle; an exchange rate is neither, and answering with a stale one would be
worse than not answering at all. See docs/PRIVACY.md.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

# --- the result -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Calculation:
    """What the calculator made of a line of text.

    ``ok`` is what tells a caller whether this is worth a little celebration: a clean sum sets it,
    a recognised-but-broken one (dividing by zero, converting a length to a weight) does not. Both
    are still shown inline rather than falling through to the assistant - once the text is
    unambiguously a calculation, sending "12/0" off as a chat prompt would be a worse answer than
    just saying what's wrong with it.
    """

    text: str
    result: str
    ok: bool = True


class _NotACalculation(Exception):
    """This text is not something the calculator handles - fall through to the assistant."""


class _CalculationError(Exception):
    """This text IS a calculation, and it failed. The message is shown, not logged or raised."""


# --- arithmetic -------------------------------------------------------------------------------
#
# A strict whitelist, checked by node TYPE rather than by trying to exclude the dangerous ones -
# anything not explicitly matched below falls through to _NotACalculation. There is no branch for
# ast.Attribute, ast.Subscript, ast.Lambda, ast.Compare, comprehensions, or ast.Call on anything
# but a bare, whitelisted name - which is what keeps a sandbox-escape one-liner like
# ``().__class__.__base__`` from ever reaching a single line of executable Python.

#: Longer than this and it is not a calculator input worth parsing at all - a real expression
#: typed by hand is a few dozen characters; anything past this is almost certainly prose, and
#: parsing it on every keystroke of a live preview would be pure waste.
MAX_INPUT_CHARS = 200

#: A single ``**`` is refused past this exponent, and a chain of them is refused once the
#: RESULT would exceed _MAX_RESULT_BITS - checked before python actually computes it, not after,
#: so a tower like ``9**9**9`` cannot spend real CPU/memory finding out it was too big.
MAX_POWER_EXPONENT = 1000
MAX_RESULT_BITS = 4096  # ~1233 decimal digits - far past any real calculator answer

_ALLOWED_UNARYOPS = {ast.UAdd: lambda x: +x, ast.USub: lambda x: -x}
_ALLOWED_NAMES = {"pi": math.pi, "e": math.e}


def _safe_round(value, ndigits=None):
    if ndigits is not None:
        if not isinstance(ndigits, (int, float)) or abs(ndigits) > 15:
            raise _CalculationError("that's more decimal places than I can round to")
        ndigits = int(ndigits)
    return round(value, ndigits)


_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": _safe_round, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
}


def _bounded(value):
    """Reject an intermediate result that has already grown past anything a calculator answer
    would look like - the backstop for any chain of operations, not just a single ``**``."""
    if isinstance(value, int) and value.bit_length() > MAX_RESULT_BITS:
        raise _CalculationError("that number's too big for me")
    return value


def _pow(left, right):
    # Only plain int**int with a non-negative exponent can blow up: a negative exponent falls
    # back to float division and a float base/exponent is bounded by IEEE overflow (an ordinary,
    # cheap `inf`), never by unbounded-precision integer arithmetic. So only this one shape needs
    # a pre-check, and it needs one BEFORE `**` runs, not after - the whole point is never to let
    # Python actually build a million-digit integer just to throw it away.
    if isinstance(left, int) and isinstance(right, int) and right >= 0:
        if right > MAX_POWER_EXPONENT:
            raise _CalculationError("that exponent's too large for me")
        if left not in (0, 1, -1) and left.bit_length() * right > MAX_RESULT_BITS:
            raise _CalculationError("that number's too big for me")
    return left**right


_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: _pow,
}


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _NotACalculation
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _bounded(_ALLOWED_BINOPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _ALLOWED_FUNCS
        and not node.keywords
    ):
        args = [_eval_node(a) for a in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
        return _ALLOWED_NAMES[node.id]
    raise _NotACalculation


def _is_trivial(node: ast.AST) -> bool:
    """A bare number with no operation applied to it - not worth intercepting on its own.

    Typing a lone ``42`` (or ``-42``, or ``pi``) into the bubble is far more likely to be a note
    to yourself than a request to be told what 42 is. Requiring at least one real operation is
    what keeps the calculator quiet on plain numbers while still catching ``6 * 7``.
    """
    while isinstance(node, ast.UnaryOp):
        node = node.operand
    return isinstance(node, (ast.Constant, ast.Name))


#: Strips comma thousand-separators, but only where they group digits in threes - "1,000,000"
#: becomes "1000000", while "min(1,2)" is untouched because "2)" is not three digits. Applied
#: before parsing, since Python's own grammar has no idea "1,000" was ever meant as one number.
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?:\D|$))")


def _try_arithmetic(text: str) -> Calculation | None:
    cleaned = _THOUSANDS_SEPARATOR.sub("", text).replace("^", "**")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except (SyntaxError, ValueError):
        return None
    if _is_trivial(tree.body):
        return None
    try:
        value = _eval_node(tree.body)
    except _NotACalculation:
        return None
    except ZeroDivisionError:
        return Calculation(text, "Can't divide by zero", ok=False)
    except _CalculationError as exc:
        return Calculation(text, str(exc), ok=False)
    except (ValueError, OverflowError, TypeError, RecursionError):
        return Calculation(text, "That doesn't have a real answer", ok=False)
    if isinstance(value, float) and not math.isfinite(value):
        return Calculation(text, "That doesn't have a real answer", ok=False)
    return Calculation(text, f"= {format_number(value)}")


# --- unit conversion --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    dimension: str
    #: Multiply a value in this unit by this to reach the dimension's base unit. Unused (and left
    #: at 1.0) for temperature, which is affine rather than linear and is converted by the two
    #: functions below instead.
    to_base: float


_UNITS: dict[str, _Unit] = {}


def _register(dimension: str, to_base: float, *names: str) -> None:
    unit = _Unit(dimension, to_base)
    for name in names:
        _UNITS[name.lower()] = unit


_register("length", 0.001, "mm", "millimeter", "millimetre", "millimeters", "millimetres")
_register("length", 0.01, "cm", "centimeter", "centimetre", "centimeters", "centimetres")
_register("length", 1.0, "m", "meter", "metre", "meters", "metres")
_register("length", 1000.0, "km", "kilometer", "kilometre", "kilometers", "kilometres")
_register("length", 0.0254, "in", "inch", "inches")
_register("length", 0.3048, "ft", "foot", "feet")
_register("length", 0.9144, "yd", "yard", "yards")
_register("length", 1609.344, "mi", "mile", "miles")
_register("length", 1852.0, "nmi", "nauticalmile", "nauticalmiles")

_register("mass", 1e-6, "mg", "milligram", "milligrams")
_register("mass", 0.001, "g", "gram", "grams")
_register("mass", 1.0, "kg", "kilogram", "kilograms")
_register("mass", 1000.0, "tonne", "tonnes", "t")
_register("mass", 0.028349523125, "oz", "ounce", "ounces")
_register("mass", 0.45359237, "lb", "lbs", "pound", "pounds")
_register("mass", 6.35029318, "stone", "st")

_register("volume", 0.001, "ml", "milliliter", "millilitre", "milliliters", "millilitres")
_register("volume", 1.0, "l", "liter", "litre", "liters", "litres")
_register("volume", 0.00492892159, "tsp", "teaspoon", "teaspoons")
_register("volume", 0.01478676478, "tbsp", "tablespoon", "tablespoons")
_register("volume", 0.2365882365, "cup", "cups")
_register("volume", 0.0295735295625, "floz", "fluidounce", "fluidounces")
_register("volume", 0.473176473, "pt", "pint", "pints")
_register("volume", 0.946352946, "qt", "quart", "quarts")
_register("volume", 3.785411784, "gal", "gallon", "gallons")

_register("time", 0.001, "ms", "millisecond", "milliseconds")
_register("time", 1.0, "s", "sec", "secs", "second", "seconds")
_register("time", 60.0, "min", "mins", "minute", "minutes")
_register("time", 3600.0, "hr", "hrs", "hour", "hours")
_register("time", 86400.0, "day", "days")
_register("time", 604800.0, "week", "weeks")

_register("speed", 1.0, "mps")
_register("speed", 0.277778, "kmh", "kph")
_register("speed", 0.44704, "mph")
_register("speed", 0.514444, "knot", "knots")

_register("data", 0.125, "bit", "bits")
_register("data", 1.0, "byte", "bytes")
_register("data", 1e3, "kb", "kilobyte", "kilobytes")
_register("data", 1e6, "mb", "megabyte", "megabytes")
_register("data", 1e9, "gb", "gigabyte", "gigabytes")
_register("data", 1e12, "tb", "terabyte", "terabytes")

_register("area", 1.0, "sqm", "m2")
_register("area", 0.09290304, "sqft", "ft2")
_register("area", 0.83612736, "sqyd", "yd2")
_register("area", 0.00064516, "sqin", "in2")
_register("area", 1e6, "sqkm", "km2")
_register("area", 2589988.110336, "sqmi", "mi2")
_register("area", 4046.8564224, "acre", "acres")
_register("area", 10000.0, "hectare", "hectares", "ha")

# A calendar year, the astronomical convention (365.25 days) - an average, not a promise that
# every year divides evenly, the same honest approximation almost every calculator makes.
_register("time", 31557600.0, "yr", "year", "years")

_register("pressure", 1.0, "pa", "pascal", "pascals")
_register("pressure", 1000.0, "kpa", "kilopascal", "kilopascals")
_register("pressure", 100000.0, "bar", "bars")
_register("pressure", 101325.0, "atm", "atmosphere", "atmospheres")
_register("pressure", 6894.757293168, "psi")
_register("pressure", 133.322387415, "mmhg", "torr")

_register("energy", 1.0, "j", "joule", "joules")
_register("energy", 1000.0, "kj", "kilojoule", "kilojoules")
_register("energy", 4.184, "cal", "calorie", "calories")
_register("energy", 4184.0, "kcal", "kilocalorie", "kilocalories")
_register("energy", 3600.0, "wh", "watthour", "watthours")
_register("energy", 3600000.0, "kwh", "kilowatthour", "kilowatthours")

_register("power", 1.0, "w", "watt", "watts")
_register("power", 1000.0, "kw", "kilowatt", "kilowatts")
_register("power", 745.699872, "hp", "horsepower")

# Base unit is the degree, not the radian - "how many degrees in 2 radians" is the everyday
# direction this gets asked in far more often than the reverse.
_register("angle", 1.0, "deg", "degree", "degrees")
_register("angle", 57.29577951308232, "rad", "radian", "radians")

# Binary storage gets its OWN names (kib/mib/...) rather than overloading kb/mb/... with a second,
# incompatible meaning - the classic 1000-vs-1024 ambiguity is real, and the fix is not to guess
# which one someone meant but to give the 1024 family unambiguous names of its own.
_register("data", 1024.0, "kib", "kibibyte", "kibibytes")
_register("data", 1024.0**2, "mib", "mebibyte", "mebibytes")
_register("data", 1024.0**3, "gib", "gibibyte", "gibibytes")
_register("data", 1024.0**4, "tib", "tebibyte", "tebibytes")

#: Ingredient -> grams per millilitre, which is identically kilograms per litre. Reference kitchen
#: values, not laboratory-precise ones - flour in particular varies with how it is packed, which is
#: exactly why this table exists at all rather than a blanket "1 ml = 1 g" shortcut: getting a
#: volume<->mass conversion right depends on knowing WHAT is being measured, and guessing wrong
#: here means someone's actual recipe comes out wrong. Sourced against published baking-conversion
#: references (King Arthur Baking and others) rather than estimated.
_DENSITY_G_PER_ML: dict[str, float] = {}


def _register_density(g_per_ml: float, *names: str) -> None:
    for name in names:
        _DENSITY_G_PER_ML[" ".join(name.lower().split())] = g_per_ml


_register_density(1.00, "water")
_register_density(1.03, "milk")
_register_density(1.42, "honey")
_register_density(0.92, "vegetable oil", "veg oil", "oil")
_register_density(0.91, "olive oil")
# The rest are derived from grams-per-US-cup baking references (King Arthur Baking and others)
# divided by the exact cup already defined above (236.5882365 ml), rather than a rounded g/ml
# figure - so "1 cup sugar" answers with exactly the 200g most baking charts cite, not 199.9-point-
# something from a shortcut that lost precision converting cups to millilitres and back.
_register_density(125 / 236.5882365, "flour", "plain flour", "all purpose flour")
_register_density(200 / 236.5882365, "sugar", "granulated sugar", "white sugar")
_register_density(220 / 236.5882365, "brown sugar")
_register_density(120 / 236.5882365, "powdered sugar", "icing sugar", "confectioners sugar")
_register_density(227 / 236.5882365, "butter")
_register_density(86 / 236.5882365, "cocoa powder", "cocoa")
_register_density(185 / 236.5882365, "rice")
_register_density(1.20, "salt", "table salt")

#: Temperature is affine (a scale AND an offset), not a simple ratio, so it cannot live in
#: ``_UNITS`` alongside everything else - "0 celsius" is not "0 of the base unit" the way
#: "0 metres" is. Each entry converts to and from Celsius, the dimension's pivot.
_TEMPERATURE: dict[str, tuple] = {
    "c": (lambda x: x, lambda c: c),
    "celsius": (lambda x: x, lambda c: c),
    "f": (lambda x: (x - 32) * 5 / 9, lambda c: c * 9 / 5 + 32),
    "fahrenheit": (lambda x: (x - 32) * 5 / 9, lambda c: c * 9 / 5 + 32),
    "k": (lambda x: x - 273.15, lambda c: c + 273.15),
    "kelvin": (lambda x: x - 273.15, lambda c: c + 273.15),
}

_CONVERSION = re.compile(
    r"^\s*(?P<value>-?[\d,]*\.?\d+)\s*(?P<from>[a-zA-Z]+)\s+(?:to|in|as)\s+(?P<to>[a-zA-Z]+)\s*$",
    re.IGNORECASE,
)

#: The same grammar with a named ingredient in the middle - "200 ml flour to grams" - tried only
#: when the plain form above does not match, since the plain form already covers every ordinary
#: conversion and this one exists solely to let volume<->mass name what is being measured. The
#: substance itself is captured permissively (one or more space-separated words) but is only ever
#: trusted after an exact lookup in ``_DENSITY_G_PER_ML`` fails or succeeds - a permissive capture
#: that must then match a short, specific table is what keeps this from firing on an ordinary
#: sentence that happens to contain "in" or "to" (see module docstring and the false-positive
#: tests in tests/smoke_calculator.py).
_DENSITY_CONVERSION = re.compile(
    r"""^\s*(?P<value>-?[\d,]*\.?\d+)\s*(?P<from>[a-zA-Z]+)\s+
    (?:of\s+)?(?P<substance>[a-zA-Z]+(?:\s[a-zA-Z]+)*?)\s+
    (?:to|in|as)\s+(?P<to>[a-zA-Z]+)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_DIMENSION_NAMES = {
    "length": "length", "mass": "weight", "volume": "volume", "time": "time",
    "speed": "speed", "data": "data size", "area": "area", "temperature": "temperature",
    "pressure": "pressure", "energy": "energy", "power": "power", "angle": "angle",
}


def _try_conversion(text: str) -> Calculation | None:
    match = _CONVERSION.match(text)
    substance: str | None = None
    if match is None:
        match = _DENSITY_CONVERSION.match(text)
        if match is None:
            return None
        substance = " ".join(match.group("substance").lower().split())
        if substance not in _DENSITY_G_PER_ML:
            return None

    try:
        value = float(_THOUSANDS_SEPARATOR.sub("", match.group("value")))
    except ValueError:
        return None
    from_key = match.group("from").lower()
    to_key = match.group("to").lower()

    from_temp = _TEMPERATURE.get(from_key)
    to_temp = _TEMPERATURE.get(to_key)
    if from_temp is not None and to_temp is not None:
        celsius = from_temp[0](value)
        answer = format_number(to_temp[1](celsius))
        return Calculation(text, f"= {answer} {match.group('to')}")

    from_unit = _UNITS.get(from_key)
    to_unit = _UNITS.get(to_key)
    # Both sides have to be units this module actually knows, or it stays quiet - one recognised
    # unit and one ordinary word is far more often a sentence than a mistake (see module docstring).
    if from_unit is None or to_unit is None:
        if from_temp is not None or to_temp is not None:
            # One side was a temperature unit and the other was not recognised at all - still a
            # clear conversion attempt, just not one between two temperatures.
            return Calculation(
                text, f"Can't convert temperature to {_pretty_dimension(to_unit, to_key)}", ok=False
            )
        return None

    if {from_unit.dimension, to_unit.dimension} == {"volume", "mass"}:
        if substance is None:
            # Genuinely ambiguous rather than impossible - volume and mass relate through
            # density, which depends on WHAT is being measured. Answering with an assumed
            # ingredient (water, say) would be confidently wrong for anything else, so this is
            # left unanswered rather than guessed at - name one, e.g. "200 ml flour to grams",
            # and it answers directly. See the module docstring.
            return None
        density = _DENSITY_G_PER_ML[substance]  # grams per ml, identically kilograms per litre
        base = value * from_unit.to_base
        base = base * density if from_unit.dimension == "volume" else base / density
        answer = format_number(base / to_unit.to_base)
        return Calculation(text, f"= {answer} {match.group('to')} of {substance}")

    if from_unit.dimension != to_unit.dimension:
        return Calculation(
            text,
            f"Can't convert {_DIMENSION_NAMES[from_unit.dimension]} to "
            f"{_DIMENSION_NAMES[to_unit.dimension]}",
            ok=False,
        )
    base = value * from_unit.to_base
    return Calculation(text, f"= {format_number(base / to_unit.to_base)} {match.group('to')}")


def _pretty_dimension(unit: _Unit | None, fallback_key: str) -> str:
    return _DIMENSION_NAMES[unit.dimension] if unit is not None else fallback_key


# --- formatting -------------------------------------------------------------------------------


def format_number(value: float) -> str:
    """A calculator answer, not a float repr - thousands separators, no scientific notation for
    anything in an everyday range, and no floating-point noise like ``4.828032000000001``."""
    if not math.isfinite(value):
        return str(value)
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1e15 or magnitude < 1e-6:
        return sign + f"{magnitude:.6g}"
    if magnitude >= 1:
        int_part_digits = len(str(int(magnitude)))
        decimals = max(0, 6 - int_part_digits)
    else:
        # A small-but-not-tiny fraction (0.001234) needs more decimal places than a flat 6 would
        # give it, or its only significant digits get rounded straight to zero.
        leading_zeros = -math.floor(math.log10(magnitude)) - 1
        decimals = leading_zeros + 6
    text = f"{magnitude:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return sign + text


# --- the public entry point --------------------------------------------------------------------


def evaluate(text: str) -> Calculation | None:
    """Read ``text`` as a plain sum or a unit conversion, or say this isn't one of those.

    ``None`` means exactly one thing: hand ``text`` on to the assistant unchanged, as if this
    module did not exist. Everything else - a clean answer, or a recognised calculation that
    failed - is returned as a :class:`Calculation` to show inline instead.
    """
    text = text.strip()
    if not text or len(text) > MAX_INPUT_CHARS:
        return None
    conversion = _try_conversion(text)
    if conversion is not None:
        return conversion
    return _try_arithmetic(text)
