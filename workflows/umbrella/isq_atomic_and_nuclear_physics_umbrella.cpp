// REQUIRES: mp-units >= 2.2
// CHURN-EXPECTED: measures the bare cost of the atomic and nuclear physics chapter; rebaseline
// when it grows. Read against the chapters it includes.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/atomic_and_nuclear_physics.h>
#endif

int main() {}
