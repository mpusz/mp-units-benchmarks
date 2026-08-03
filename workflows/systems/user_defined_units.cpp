// Defining a system, rather than consuming one. umbrella/ measures what including SI costs; this
// measures what DECLARING units costs: named units, prefixed units, units built from magnitudes and
// from other units, a quantity-spec tree to hang them on, and constants expressed as units - which
// is the work a domain library does once and every downstream translation unit pays for.
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

namespace storage {

using namespace mp_units;

// A quantity tree of its own, so the units below have something to be units *of*.
QUANTITY_SPEC(data, dimensionless, is_kind);
QUANTITY_SPEC(transfer_rate, data / isq::time);
QUANTITY_SPEC(latency, isq::duration);
QUANTITY_SPEC(seek_distance, isq::length);

// Named base unit, then binary and decimal prefixes over it.
inline constexpr struct bit final : named_unit<"bit", one, kind_of<data>> {} bit;
inline constexpr struct byte final : named_unit<"B", mag<8> * bit> {} byte;
inline constexpr auto kibibyte = mag_power<2, 10> * byte;   // binary magnitudes, declared here
inline constexpr auto mebibyte = mag_power<2, 20> * byte;   // rather than borrowed from iec/
inline constexpr auto kilobyte = si::kilo<byte>;            // and a decimal prefix over the same unit

// Units derived from other units rather than declared outright.
inline constexpr struct bytes_per_second final : named_unit<"B/s", byte / si::second> {} bytes_per_second;
inline constexpr auto mebibytes_per_second = mebibyte / si::second;

// A unit scaled by an awkward magnitude, which is how physical constants are expressed as units.
inline constexpr struct sector final : named_unit<"sector", mag<512> * byte> {} sector;
inline constexpr struct rotation_period final : named_unit<"rot", mag_ratio<1, 120> * si::second> {} rotation_period;

}  // namespace storage

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity payload = 3.0 * storage::mebibyte;
  const quantity chunk = 64.0 * storage::sector;
  const quantity rate = 180.0 * storage::bytes_per_second;
  const quantity spin = 7.0 * storage::rotation_period;

  const quantity chunks = payload / chunk;
  const quantity elapsed = payload / rate;
  const quantity in_decimal = payload.in(storage::kilobyte);
  const quantity fast = rate.in(storage::mebibytes_per_second);

  std::printf("%f %f %f %f %f\n", chunks.numerical_value_in(one), elapsed.numerical_value_in(s),
              in_decimal.numerical_value_in(storage::kilobyte), fast.numerical_value_in(storage::mebibytes_per_second),
              spin.numerical_value_in(s));
}
