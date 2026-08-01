// Conversions along and across ISQ hierarchy branches, incl. common quantity specs.
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

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity width = isq::width(2.0 * m);
  const quantity height = isq::height(3.0 * m);
  quantity<isq::length[m]> len = width; // upcast (implicit)
  len = height;
  const quantity radius = isq::radius(0.5 * m);
  const quantity common = width + radius; // common quantity spec
  const quantity energy = isq::potential_energy(10.0 * J) + isq::kinetic_energy(5.0 * J);
  const quantity speed = isq::speed(std::int64_t{10} * m / s);
  const quantity height2 = quantity_cast<isq::height>(len); // explicit downcast
  std::printf("%f %f %f %d\n", common.numerical_value_in(m), energy.numerical_value_in(J),
              speed.in<double>(km / h).numerical_value_in(km / h),
              static_cast<int>(height2.numerical_value_in(m)));
}
