// REQUIRES: mp-units >= 2.2
// The framework intercept: what a translation unit pays for the core machinery alone, before any
// system defines a single unit, quantity spec or constant. Every umbrella row is read against this
// number - (umbrella - core_only) / entities is the cost per defined entity, and this row is the
// only way to separate "the framework got dearer" from "a system grew". It defines nothing, so its
// entity census is empty by construction.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/framework.h>
#endif

int main() {}
