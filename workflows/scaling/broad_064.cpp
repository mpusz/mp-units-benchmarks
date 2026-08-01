// Scaling series: the marginal cost of user code, not of pulling the library in. Read this row
// against the other sizes of the same shape - the difference divided by the difference in steps is
// what one more operation costs. Every step uses a different unit, so each one demands its own
// specializations - this is what grows the instantiation table.
#include "scaling_workload.h"
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#include <utility>
#endif

int main()
{
  std::printf("%f\n", scaling::run<scaling::broad>(std::make_index_sequence<64>{}));
}
