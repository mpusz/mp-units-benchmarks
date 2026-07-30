// Quantity text output via output streams - `operator<<` on the same workload the rest of the
// text/ family prints.
#include "output_workload.h"
#if MP_UNITS_BENCH_VERSION < 205
#include <mp-units/ostream.h>  // streaming moved into the framework headers in 2.5
#endif
#include <iostream>

int main()
{
  std::cout << readings::speed << '\n';
  std::cout << readings::acceleration << '\n';
  std::cout << readings::power << '\n';
  std::cout << readings::energy << '\n';
}
