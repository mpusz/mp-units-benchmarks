// REQUIRES: mp-units >= 2.5
// Overload sets constrained with `QuantityOf`, one candidate per quantity. Overload resolution
// evaluates the constraint on every candidate, so each call here pays for `QuantityOf` evaluating to
// false 17 times and to true once: the false branches, which the kind-safe interfaces of the other
// workflows never take. The same quantities are then added, subtracted and compared in pairs of
// units, the accepted path of the two-quantity operators for 18 distinct pairs of quantity types.
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

constexpr int describe(QuantityOf<isq::length> auto) { return 1; }
constexpr int describe(QuantityOf<isq::duration> auto) { return 2; }
constexpr int describe(QuantityOf<isq::mass> auto) { return 3; }
constexpr int describe(QuantityOf<isq::area> auto) { return 4; }
constexpr int describe(QuantityOf<isq::volume> auto) { return 5; }
constexpr int describe(QuantityOf<isq::speed> auto) { return 6; }
constexpr int describe(QuantityOf<isq::acceleration> auto) { return 7; }
constexpr int describe(QuantityOf<isq::frequency> auto) { return 8; }
constexpr int describe(QuantityOf<isq::angular_measure> auto) { return 9; }
constexpr int describe(QuantityOf<isq::angular_velocity> auto) { return 10; }
constexpr int describe(QuantityOf<isq::force> auto) { return 11; }
constexpr int describe(QuantityOf<isq::energy> auto) { return 12; }
constexpr int describe(QuantityOf<isq::power> auto) { return 13; }
constexpr int describe(QuantityOf<isq::momentum> auto) { return 14; }
constexpr int describe(QuantityOf<isq::pressure> auto) { return 15; }
constexpr int describe(QuantityOf<isq::mass_density> auto) { return 16; }
constexpr int describe(QuantityOf<isq::moment_of_force> auto) { return 17; }
constexpr int describe(QuantityOf<isq::dynamic_viscosity> auto) { return 18; }

// The same quantity in two units: `lhs + rhs`, `lhs - rhs`, `lhs == rhs` and `lhs < rhs` each form
// the common reference of a distinct pair of quantity types.
template<typename Q1, typename Q2>
constexpr int mix(const Q1& lhs, const Q2& rhs)
{
  return describe(lhs + rhs) + describe(lhs - rhs) + (lhs == rhs) + (lhs < rhs);
}

int main()
{
  using namespace mp_units::si::unit_symbols;

  const quantity len_m = isq::length(1.0 * m);
  const quantity len_km = isq::length(1.0 * km);
  const quantity dur_s = isq::duration(1.0 * s);
  const quantity dur_h = isq::duration(1.0 * h);
  const quantity mass_kg = isq::mass(1.0 * kg);
  const quantity mass_g = isq::mass(1.0 * g);
  const quantity area_m = isq::area(1.0 * m2);
  const quantity area_km = isq::area(1.0 * square(km));
  const quantity vol_m = isq::volume(1.0 * m3);
  const quantity vol_l = isq::volume(1.0 * l);
  const quantity spd_ms = isq::speed(1.0 * m / s);
  const quantity spd_kmh = isq::speed(1.0 * km / h);
  const quantity acc_ms = isq::acceleration(1.0 * m / s2);
  const quantity acc_kmhs = isq::acceleration(1.0 * km / h / s);
  const quantity freq_hz = isq::frequency(1.0 * Hz);
  const quantity freq_khz = isq::frequency(1.0 * kHz);
  const quantity ang_rad = isq::angular_measure(1.0 * rad);
  const quantity ang_deg = isq::angular_measure(1.0 * deg);
  const quantity angv_rads = isq::angular_velocity(1.0 * rad / s);
  const quantity angv_degmin = isq::angular_velocity(1.0 * deg / min);
  const quantity force_n = isq::force(1.0 * N);
  const quantity force_kn = isq::force(1.0 * kN);
  const quantity en_j = isq::energy(1.0 * J);
  const quantity en_kwh = isq::energy(1.0 * kW * h);
  const quantity pow_w = isq::power(1.0 * W);
  const quantity pow_mw = isq::power(1.0 * MW);
  const quantity mom_si = isq::momentum(1.0 * kg * m / s);
  const quantity mom_cgs = isq::momentum(1.0 * g * km / h);
  const quantity pres_pa = isq::pressure(1.0 * Pa);
  const quantity pres_kpa = isq::pressure(1.0 * kPa);
  const quantity dens_si = isq::mass_density(1.0 * kg / m3);
  const quantity dens_gl = isq::mass_density(1.0 * g / l);
  const quantity torque_nm = isq::moment_of_force(1.0 * N * m);
  const quantity torque_knmm = isq::moment_of_force(1.0 * kN * mm);
  const quantity visc_pas = isq::dynamic_viscosity(1.0 * Pa * s);
  const quantity visc_kpams = isq::dynamic_viscosity(1.0 * kPa * ms);

  const int chosen = describe(len_m) + describe(dur_s) + describe(mass_kg) + describe(area_m) + describe(vol_m) +
                     describe(spd_ms) + describe(acc_ms) + describe(freq_hz) + describe(ang_rad) +
                     describe(angv_rads) + describe(force_n) + describe(en_j) + describe(pow_w) +
                     describe(mom_si) + describe(pres_pa) + describe(dens_si) + describe(torque_nm) +
                     describe(visc_pas);
  const int mixed = mix(len_m, len_km) + mix(dur_s, dur_h) + mix(mass_kg, mass_g) + mix(area_m, area_km) +
                    mix(vol_m, vol_l) + mix(spd_ms, spd_kmh) + mix(acc_ms, acc_kmhs) + mix(freq_hz, freq_khz) +
                    mix(ang_rad, ang_deg) + mix(angv_rads, angv_degmin) + mix(force_n, force_kn) +
                    mix(en_j, en_kwh) + mix(pow_w, pow_mw) + mix(mom_si, mom_cgs) + mix(pres_pa, pres_kpa) +
                    mix(dens_si, dens_gl) + mix(torque_nm, torque_knmm) + mix(visc_pas, visc_kpams);
  std::printf("%d %d\n", chosen, mixed);
}
