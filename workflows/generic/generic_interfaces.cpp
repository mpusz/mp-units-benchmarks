// Generic code over references and representation types - templates any unit/rep combination.
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#include <cstdio>

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
