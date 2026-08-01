// Quantity text output via output streams - `operator<<` on the same workload the rest of the
// text/ family prints.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <iostream>
#endif
#include "output_workload.h"
#ifndef MP_UNITS_MODULES  // the module exports these; only a header build needs the legacy path
#if MP_UNITS_BENCH_VERSION < 205
#include <mp-units/ostream.h>  // streaming moved into the framework headers in 2.5
#endif
#endif

int main()
{
  std::cout << readings::speed << '\n';
  std::cout << readings::acceleration << '\n';
  std::cout << readings::power << '\n';
  std::cout << readings::energy << '\n';
}
