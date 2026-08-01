// Scaling series: the marginal cost of user code, not of pulling the library in. Read this row
// against the other sizes of the same shape - the difference divided by the difference in steps is
// what one more operation costs. Every step works in the same five quantity types, so the library's
// specializations are already warm and the step only adds code - what a production file looks like.
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
  std::printf("%f\n", scaling::run<scaling::narrow>(std::make_index_sequence<256>{}));
}
