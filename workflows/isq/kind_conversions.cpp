// `kind_of` at the boundaries: a unit-only reference produces a kind-of quantity, and pinning it to
// a specific quantity spec - or accepting either in a generic interface - is what a library author
// writes at every API edge. hierarchy_conversions covers conversions between specs; this covers the
// step before, where a quantity has a kind rather than a spec.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/mechanics.h>
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

using namespace mp_units;

// Accepts anything of the length kind: a bare `1 * m`, an `isq::width`, an `isq::radius`.
constexpr auto to_millimetres(QuantityOf<kind_of<isq::length>> auto q) { return q.in(si::milli<si::metre>); }

// Insists on a specific spec instead of the kind, so a plain unit-only quantity has to be pinned.
constexpr auto elapsed(QuantityOf<isq::duration> auto t) { return t.in(si::second); }

int main()
{
  using namespace mp_units::si::unit_symbols;

  // Unit-only references carry `kind_of<...>`, not a specific quantity spec.
  const quantity bare_length = 2.5 * m;
  const quantity bare_time = 90.0 * s;

  // The kind-constrained interface takes both the bare quantity and specific specs.
  const quantity a = to_millimetres(bare_length);
  const quantity b = to_millimetres(isq::width(0.75 * m));
  const quantity c = to_millimetres(isq::radius(30.0 * cm));

  // The spec-constrained one needs the kind pinned down first.
  const quantity d = elapsed(isq::duration(bare_time));

  // A kind-of quantity mixes with a specific one; the result keeps the specific spec.
  const quantity speed = isq::height(12.0 * m) / bare_time;
  const quantity generic_speed = bare_length / bare_time;

  std::printf("%f %f %f %f %f %f\n", a.numerical_value_in(mm), b.numerical_value_in(mm),
              c.numerical_value_in(mm), d.numerical_value_in(s), speed.numerical_value_in(m / s),
              generic_speed.numerical_value_in(m / s));
}
