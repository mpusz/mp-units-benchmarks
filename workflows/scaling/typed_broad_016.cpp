// REQUIRES: mp-units >= 2.2
// Scaling series: broad's computation with TYPED quantities - one distinct derived STRONG quantity
// per step. This slope minus broad's slope is the level-5 abstraction tax per derived quantity.
// See typed_workload.h for what a step is and why the two must stay in lockstep.
#include "typed_workload.h"
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif

int main()
{
  std::printf("%f\n", scaling_typed::run(std::make_index_sequence<16>{}));
}
