// REQUIRES: mp-units >= 2.2
// Scaling series: the marginal cost of composing a DISTINCT derived quantity spec per step - the
// equation pipeline at the use site, where 'broad' measures derived units. Read this row against
// the other sizes of the same shape; see spec_workload.h for what a step is.
#include "spec_workload.h"
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif

int main()
{
  std::printf("%zu\n", scaling_specs::run(std::make_index_sequence<64>{}));
}
