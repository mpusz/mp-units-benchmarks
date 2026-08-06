// CHURN-EXPECTED: measures the bare cost of the LEAN si include; rebaseline when SI grows.
// Its counterpart si_umbrella.cpp includes the full <mp-units/systems/si.h>; the difference
// between the two is what a translation unit pays for the parts most code does not use -
// the full 24-prefix symbol matrix, the SI defining constants, <chrono>, and <cmath>.
// Without this workflow the suite cannot see that difference move, because every other
// workflow includes the full umbrella.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/si/core.h>
#endif

int main() {}
