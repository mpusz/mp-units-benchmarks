// REQUIRES: mp-units >= 2.2
// CHURN-EXPECTED: measures the bare cost of the thermodynamics chapter; rebaseline when it grows.
// Includes mechanics (and through it space_and_time): read its own cost as the diff against
// isq_mechanics_umbrella.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/thermodynamics.h>
#endif

int main() {}
