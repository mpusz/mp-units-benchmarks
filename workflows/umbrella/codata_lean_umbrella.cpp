// REQUIRES: mp-units >= 2.6
// CHURN-EXPECTED: measures the bare cost of the ESSENTIAL codata tier; rebaseline when the tables are
// regenerated. This is the NIST "frequently used constants" selection - 25 definitions - and the two
// workflows above it (codata_2022_umbrella, codata_umbrella) are the same header plus the rest of one
// adjustment and plus two more adjustments. Read the three against each other: the differences are what
// a constant definition costs and what a second, historical table of the same constants costs again.
// Nothing else in the corpus is dominated by distinct MAGNITUDES - scaling/broad deliberately holds them
// fixed so its slope measures unit diversity - so this series is where compile-time factorization shows.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/codata/codata2022_essential.h>
#endif

int main() {}
