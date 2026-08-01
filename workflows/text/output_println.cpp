// Quantity text output via std::println - the same workload as the rest of the text/ family.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <print>
#endif
#include "output_workload.h"
#ifndef MP_UNITS_MODULES  // the module exports these; only a header build needs the legacy path
#if MP_UNITS_BENCH_VERSION < 205
#include <mp-units/format.h>  // formatter specializations moved into the framework in 2.5
#endif
#endif

int main()
{
  std::println("{}", readings::speed);
  std::println("{}", readings::acceleration);
  std::println("{}", readings::power);
  std::println("{}", readings::energy);
}
