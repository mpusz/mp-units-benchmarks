// REQUIRES: mp-units >= 2.6
// Computing WITH measured constants, which is the only reason to have them. The umbrella workflows
// price the declarations; the difference between this file and umbrella/codata_lean_umbrella - the
// very header it includes - is what the expressions below cost on top of them.
//
// A constant is a unit whose magnitude is an arbitrary rational, so every product of two of them is a
// derived unit the framework has never seen and every conversion to a plain SI unit is a magnitude
// arithmetic problem rather than a table lookup. That is the axis this file adds: the rest of the
// corpus composes units out of a handful of small magnitudes, and scaling/broad holds magnitudes fixed
// on purpose so that its slope measures unit diversity instead of prime factorization.
//
// The lean tier is included deliberately: a workflow that included <mp-units/systems/codata.h> would
// spend an order of magnitude more on definitions it never names, and the use cost would vanish inside
// the inclusion cost.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#include <numbers>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/codata/codata2022_essential.h>
#include <mp-units/systems/si.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;
  namespace cd = mp_units::codata;

  // A photon's energy from its wavelength: two constants multiplied, then converted to a unit from a
  // different system than either of them.
  const quantity wavelength = 532.0 * nm;
  const quantity photon_energy = (1. * cd::planck_constant * cd::speed_of_light_in_vacuum) / wavelength;
  const quantity in_ev = photon_energy.in(si::electronvolt);

  // de Broglie: a constant divided by the product of a constant and a plain quantity.
  const quantity electron_speed = 2.2e6 * m / s;
  const quantity de_broglie = (1. * cd::planck_constant) / (1. * cd::electron_mass * electron_speed);

  // Cyclotron frequency: charge and mass, both constants, meeting a field expressed in SI.
  const quantity field = 1.5 * T;
  const quantity cyclotron = (1. * cd::elementary_charge * field) / (1. * cd::electron_mass);

  // Newtonian gravity, where the constant carries the composite unit m^3 kg^-1 s^-2.
  const quantity earth_mass = 5.9722e24 * kg;
  const quantity probe_mass = 720.0 * kg;
  const quantity orbit = 6.771e6 * m;
  const quantity pull = (1. * cd::newtonian_constant_of_gravitation * earth_mass * probe_mass) / (orbit * orbit);

  // Thermal energy at room temperature, and the same energy per mole - the two sides of Boltzmann and
  // the molar gas constant, which differ by the Avogadro constant.
  const quantity temperature = delta<K>(293.15);
  const quantity thermal = 1. * cd::boltzmann_constant * temperature;
  const quantity molar = 1. * cd::molar_gas_constant * temperature;

  // Ratios of constants that are dimensionless by construction: the framework has to cancel the units
  // rather than convert them.
  const quantity mass_ratio = (1. * cd::proton_mass) / (1. * cd::electron_mass);
  const quantity alpha = ((1. * cd::elementary_charge * cd::elementary_charge) /
                          (4. * std::numbers::pi * (1. * cd::vacuum_electric_permittivity) *
                           (1. * cd::reduced_planck_constant * cd::speed_of_light_in_vacuum)))
                           .in(one);

  // A constant used as the unit of the result, rather than converted away from: an atomic-scale length
  // expressed in Bohr radii, and a mass in atomic mass constants.
  const quantity radius = (0.74e-10 * m).in(cd::bohr_radius);
  const quantity nuclide = (1.9926e-26 * kg).in(cd::atomic_mass_constant);

  std::printf("%f %f %f %f %f %f %f %f %f %f\n", in_ev.numerical_value_in(si::electronvolt),
              de_broglie.numerical_value_in(pm), cyclotron.numerical_value_in(GHz),
              pull.numerical_value_in(N), thermal.numerical_value_in(si::milli<si::electronvolt>),
              molar.numerical_value_in(J / mol), mass_ratio.numerical_value_in(one),
              alpha.numerical_value_in(one), radius.numerical_value_in(cd::bohr_radius),
              nuclide.numerical_value_in(cd::atomic_mass_constant));
}
