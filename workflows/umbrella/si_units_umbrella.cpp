// REQUIRES: mp-units >= 2.2
// CHURN-EXPECTED: measures the bare cost of SI's unit DEFINITIONS (si/units.h) - the named units
// with their kinds, but no unit symbols, no prefix matrix, no constants, no <chrono>. Against
// control/core_only this is the purest named-unit axis the corpus has (the census subtracts the
// si_quantities specs it pulls in), and against si_lean_umbrella the difference is the SYMBOL TAX:
// what `si/core.h` pays on top of the definitions for the unit_symbols namespace and the prefix
// machinery most code wants anyway. That decomposition is why this workflow exists - "an SI unit
// costs 62 instantiations" turned out to be mostly ecosystem, and this row prices the parts.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/si/units.h>
#endif

int main() {}
