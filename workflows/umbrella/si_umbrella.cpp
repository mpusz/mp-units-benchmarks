// CHURN-EXPECTED: measures the bare cost of the si.h umbrella; rebaseline when SI grows.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/si.h>
#endif

int main() {}
