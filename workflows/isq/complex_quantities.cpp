// REQUIRES: mp-units >= 2.6
// Complex-field quantities: AC circuit analysis in phasor form, where the representation is
// std::complex and the quantity specs opt into quantity_field::complex. The scalar workflows never
// touch that field machinery, and it is what an electrical-engineering domain actually uses.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <complex>
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/electromagnetism.h>
#include <mp-units/systems/si.h>
#endif

namespace ac {

using namespace mp_units;

// Phasors: same dimensions as their real counterparts, complex field.
QUANTITY_SPEC(voltage_phasor, isq::voltage, quantity_field::complex);
QUANTITY_SPEC(current_phasor, isq::electric_current, quantity_field::complex);
QUANTITY_SPEC(impedance, isq::resistance, quantity_field::complex);

}  // namespace ac

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;
  using c = std::complex<double>;

  const quantity supply = c{230.0, 0.0} * ac::voltage_phasor[V];
  const quantity resistive = c{12.0, 0.0} * ac::impedance[si::ohm];
  const quantity reactive = c{0.0, 7.5} * ac::impedance[si::ohm];
  const quantity total = resistive + reactive;
  const quantity current = supply / total;
  const quantity dropped = current * resistive;

  std::printf("%f %f %f\n", std::abs(current.numerical_value_in(A)),
              std::arg(current.numerical_value_in(A)), std::abs(dropped.numerical_value_in(V)));
}
