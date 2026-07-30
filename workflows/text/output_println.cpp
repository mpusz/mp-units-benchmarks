// Quantity text output via std::println - the same workload as the rest of the text/ family.
#include "output_workload.h"
#if MP_UNITS_BENCH_VERSION < 205
#include <mp-units/format.h>  // formatter specializations moved into the framework in 2.5
#endif
#include <print>

int main()
{
  std::println("{}", readings::speed);
  std::println("{}", readings::acceleration);
  std::println("{}", readings::power);
  std::println("{}", readings::energy);
}
