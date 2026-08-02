// Fractional dimensional exponents: units and quantity specs raised to rational powers, which the
// rest of the corpus never produces. Amplifier noise is the canonical domain - spectral density is
// V/sqrt(Hz), so every step carries a half-power through the unit and quantity-spec arithmetic.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/math.h>
#include <mp-units/systems/isq/electromagnetism.h>
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  // Input-referred noise density of an amplifier, integrated over its bandwidth.
  const quantity density = 4.5e-9 * (V / sqrt(Hz));
  const quantity bandwidth = 20.0e3 * Hz;
  const quantity noise = density * sqrt(bandwidth);

  // The same half-power reached the other way: from a mean square rather than a density.
  const quantity mean_square = pow<2>(noise);
  const quantity rms = sqrt(mean_square);

  // A cube root, so the exponent arithmetic is not only halves.
  const quantity volume = 8.0 * pow<3>(m);
  const quantity side = cbrt(volume);

  // A rational power that is neither 1/2 nor 1/3, forcing general exponent handling.
  const quantity odd = pow<2, 3>(volume);

  std::printf("%g %g %g %g\n", noise.numerical_value_in(V), rms.numerical_value_in(V),
              side.numerical_value_in(m), odd.numerical_value_in(pow<2>(m)));
}
