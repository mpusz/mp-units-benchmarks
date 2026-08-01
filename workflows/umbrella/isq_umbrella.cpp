// CHURN-EXPECTED: measures the bare cost of the isq.h umbrella; rebaseline when ISQ grows.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq.h>
#endif

int main() {}
