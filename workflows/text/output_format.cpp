// Quantity text output via std::format - the same workload as the rest of the text/ family.
// Output goes through <cstdio> rather than <iostream> on purpose: pulling in the stream
// machinery here would blur the comparison against output_ostream.
#include "output_workload.h"
#if MP_UNITS_BENCH_VERSION < 205
#include <mp-units/format.h>  // formatter specializations moved into the framework in 2.5
#endif
#include <cstdio>
#include <format>

int main()
{
  std::fputs(std::format("{}\n", readings::speed).c_str(), stdout);
  std::fputs(std::format("{}\n", readings::acceleration).c_str(), stdout);
  std::fputs(std::format("{}\n", readings::power).c_str(), stdout);
  std::fputs(std::format("{}\n", readings::energy).c_str(), stdout);
}
