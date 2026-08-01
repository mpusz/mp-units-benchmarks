// REQUIRES: mp-units >= 2.5
// Generic code over references and representation types - templates any unit/rep combination.
// (2.4 enforced character/representation matching, so `isq::acceleration` with a `double` rep
// is ill-formed there - a rewrite would measure a different workload, so 2.4 is reported n/a.)
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

using namespace mp_units;

template <typename Rep, Reference R>
constexpr quantity<R{}, Rep> parse_reading(double raw, R r)
{
  return static_cast<Rep>(raw) * r;
}

constexpr QuantityOf<isq::length> auto brake_distance(QuantityOf<isq::speed> auto v, QuantityOf<isq::acceleration> auto a)
{
  return v * v / (2 * a);
}

int main()
{
  using namespace mp_units::si::unit_symbols;

  const quantity v1 = parse_reading<double>(120.0, km / h);
  const quantity v2 = parse_reading<float>(33.3, m / s);
  const quantity v3 = parse_reading<std::int64_t>(120.0, km / h);
  const quantity a = isq::acceleration(7.5 * m / s2);
  std::printf("%f %f %f\n", brake_distance(v1, a).numerical_value_in(m),
              static_cast<double>(brake_distance(v2, a).numerical_value_in(m)),
              brake_distance(isq::speed(v3).in<double>(m / s), a).numerical_value_in(m));
}
