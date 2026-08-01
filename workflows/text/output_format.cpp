// Quantity text output via std::format - the same workload as the rest of the text/ family.
// Output goes through C stdio rather than <iostream> on purpose: pulling in the stream machinery
// here would blur the comparison against output_ostream. The sink is opened explicitly because
// `stdout` is a macro from <cstdio>, so it does not exist under `import std;`.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#include <format>
#endif
#include "output_workload.h"
#ifndef MP_UNITS_MODULES  // the module exports these; only a header build needs the legacy path
#if MP_UNITS_BENCH_VERSION < 205
#include <mp-units/format.h>  // formatter specializations moved into the framework in 2.5
#endif
#endif

int main()
{
  std::FILE* out = std::fopen("/dev/stdout", "w");
  std::fputs(std::format("{}\n", readings::speed).c_str(), out);
  std::fputs(std::format("{}\n", readings::acceleration).c_str(), out);
  std::fputs(std::format("{}\n", readings::power).c_str(), out);
  std::fputs(std::format("{}\n", readings::energy).c_str(), out);
  std::fclose(out);
}
