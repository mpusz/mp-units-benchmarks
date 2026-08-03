// Value conversions: the scaling engine, which the corpus otherwise reaches only incidentally.
// Integer representations force the rational-magnitude path (widening, overflow reasoning), and
// value_cast / force_in are the explicit escapes a user needs when a conversion is lossy.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdint>
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/si.h>
#include <mp-units/systems/usc.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  // Integer quantities: every conversion below goes through the rational-magnitude path rather than
  // a floating-point multiply.
  const quantity distance = std::int32_t{4200} * m;
  const quantity whole = value_cast<si::kilo<si::metre>>(distance);
  const quantity truncating = distance.force_in(si::kilo<si::metre>);

  // Representation changes, in both directions.
  const quantity widened = value_cast<std::int64_t>(distance);
  const quantity as_double = distance.in<double>(si::kilo<si::metre>);
  const quantity narrowed = value_cast<float>(as_double);

  // A unit and a representation at once, which is the two-argument form.
  const quantity both = value_cast<si::centi<si::metre>, std::int64_t>(distance);

  // Across systems, where the magnitude is an awkward ratio rather than a power of ten.
  const quantity imperial = distance.in<double>(usc::foot);
  const quantity back = value_cast<si::metre>(imperial);

  std::printf("%d %d %ld %f %f %ld %f %f\n", whole.numerical_value_in(km),
              truncating.numerical_value_in(km), widened.numerical_value_in(m),
              as_double.numerical_value_in(km), static_cast<double>(narrowed.numerical_value_in(km)),
              both.numerical_value_in(si::centi<si::metre>), imperial.numerical_value_in(usc::foot),
              back.numerical_value_in(m));
}
