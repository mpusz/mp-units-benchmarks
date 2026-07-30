// Strongly-typed APIs constrained on quantity kinds - the safety layer other libraries lack.
#include <mp-units/math.h>
#include <mp-units/systems/isq/mechanics.h>
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#include <cstdio>

using namespace mp_units;

constexpr QuantityOf<isq::speed> auto avg_speed(QuantityOf<isq::length> auto d,
                                                QuantityOf<isq::duration> auto t)
{
  return d / t;
}

constexpr QuantityOf<isq::kinetic_energy> auto kinetic_energy(QuantityOf<isq::mass> auto m,
                                                              QuantityOf<isq::speed> auto v)
{
  return isq::kinetic_energy(m * pow<2>(v) / 2);
}

constexpr QuantityOf<isq::power> auto mean_power(QuantityOf<isq::energy> auto e,
                                                 QuantityOf<isq::duration> auto t)
{
  return e / t;
}

int main()
{
  using namespace mp_units::si::unit_symbols;

  const quantity v = avg_speed(isq::distance(220.0 * km), 2.0 * h);
  const quantity e = kinetic_energy(1400.0 * kg, v);
  const quantity p = mean_power(e, 10.0 * s);
  std::printf("%f\n", p.numerical_value_in(kW));
}
