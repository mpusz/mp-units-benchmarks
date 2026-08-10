// REQUIRES: mp-units >= 2.6
// CHURN-EXPECTED: measures the bare cost of the codata.h umbrella - all three adjustments; rebaseline
// when the tables are regenerated or an adjustment is added. Against codata_2022_umbrella the difference
// is what 2014 and 2018 cost on top of 2022: the constants are the same physical quantities in the same
// units, differing only in their measured values, so this is what the framework can and cannot share
// between two magnitudes that are close in value and unrelated in factorization. It is also what every
// MODULES consumer pays whether or not it names a constant - the systems module interface includes this
// umbrella, so under `import mp_units;` the whole table is in the BMI.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/codata.h>
#endif

int main() {}
