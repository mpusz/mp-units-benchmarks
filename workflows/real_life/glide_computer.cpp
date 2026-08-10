// REQUIRES: mp-units >= 2.6
// Frozen snapshot of mp-units' example/glide_computer (as of a742af8c4): geographic.h,
// glide_computer_lib.h/.cpp and glide_computer.cpp flattened into one TU, licence boilerplate and
// cross-includes dropped, preambles merged into the corpus convention. At ~950 lines of kind-safe
// geographic coordinates (bounded point origins, frame projections between azimuth conventions),
// custom formatters, chrono interop, ranges algorithms over vectors of typed quantities and a
// simulation loop, this is the closest thing the corpus has to a production-shaped TU - though
// still an example: the tier prices the feature MIX in one file, and SIZE remains the scaling
// series' axis. Deliberately FROZEN rather than included from the measured checkout: a workflow
// whose source changes with the ref compares different programs and calls it a regression.
#include <mp-units/compat_macros.h>
#include <mp-units/bits/hacks.h>
#include <mp-units/ext/format.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <algorithm>
#include <array>
#include <chrono>
#include <compare>
#include <concepts>
#include <cstddef>
#include <exception>
#include <functional>
#include <initializer_list>
#include <iostream>
#include <iterator>
#include <limits>
#include <numbers>
#include <numeric>
#include <ostream>
#include <ranges>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/framework.h>
#include <mp-units/math.h>
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#include <mp-units/systems/yard_pound.h>
#endif

namespace geographic {

inline constexpr struct mean_sea_level final : mp_units::absolute_point_origin<mp_units::isq::altitude> {
} mean_sea_level;

using msl_altitude = mp_units::quantity_point<mp_units::isq::altitude[mp_units::si::metre], mean_sea_level>;

// text output
template<class CharT, class Traits>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const msl_altitude& a)
{
  return os << a - mean_sea_level << " AMSL";
}

}  // namespace geographic

template<typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::msl_altitude, Char> :
    formatter<geographic::msl_altitude::quantity_type, Char> {
  template<typename FormatContext>
  auto format(const geographic::msl_altitude& a, FormatContext& ctx) const -> decltype(ctx.out())
  {
    ctx.advance_to(
      formatter<geographic::msl_altitude::quantity_type, Char>::format(a - geographic::mean_sea_level, ctx));
    return MP_UNITS_STD_FMT::format_to(ctx.out(), " AMSL");
  }
};

namespace geographic {

// quantity specifications for geographic coordinates and orientation angles
//
// Geographic coordinates use different wrapping behaviors:
// - latitude: symmetric/reflects at ±90° (can't go past poles)
// - longitude: mirrored wrapping [-180°, 180°) — half-open interval, 180° wraps to -180°
// - elevation: symmetric/reflects at ±90° (like latitude)
//
// Orientation angles have different zero references and rotation directions.
// All use mirrored wrapping [-180°, 180°) — half-open interval where max is exclusive:
// - geometric_azimuth: 0° = East, increases counter-clockwise
// - bearing: 0° = North, increases clockwise
//   Conversion: bearing = 90° - geometric_azimuth
// - heading_azimuth: 0° = North, increases counter-clockwise
//   Conversion: heading = geometric_azimuth - 90°
//
// All geographic quantity specs are marked with is_kind to prevent accidental mixing
// (e.g., latitude + longitude, bearing + heading) and to require explicit conversions
// between different angle reference frames.
//
// For trigonometric functions (sin/cos/etc.), explicit conversion to angular_measure is needed:
//   const quantity<angular_measure> angle = isq::angular_measure(lat.quantity_from(equator));
//   sin(angle);  // Now works with plain angular_measure

// Basic geographic coordinates
QUANTITY_SPEC(geo_latitude, mp_units::isq::angular_measure, mp_units::is_kind);
QUANTITY_SPEC(geo_longitude, mp_units::isq::angular_measure, mp_units::is_kind);
QUANTITY_SPEC(geo_elevation, mp_units::isq::angular_measure, mp_units::is_kind);

// Orientation angles (different zero references and rotation directions)
QUANTITY_SPEC(geometric_azimuth, mp_units::isq::angular_measure, mp_units::is_kind);
QUANTITY_SPEC(geo_bearing, mp_units::isq::angular_measure, mp_units::is_kind);
QUANTITY_SPEC(heading_azimuth, mp_units::isq::angular_measure, mp_units::is_kind);

// Note: equator carries no `reflect_in_range` bounds. Latitude reflection at the
// poles is coupled with a 180° longitude shift, which a single-axis policy
// cannot express. The coupled normalization lives in `position`'s constructor.
inline constexpr struct equator final : mp_units::absolute_point_origin<geo_latitude> {
} equator;

inline constexpr struct prime_meridian final :
    mp_units::absolute_point_origin<geo_longitude,
                                    mp_units::wrap_to_range{-180 * mp_units::si::degree, 180 * mp_units::si::degree}> {
} prime_meridian;

inline constexpr struct horizon final :
    mp_units::absolute_point_origin<geo_elevation,
                                    mp_units::reflect_in_range{-90 * mp_units::si::degree, 90 * mp_units::si::degree}> {
} horizon;

// Geometric azimuth: 0° = East, counter-clockwise positive, mirrored wrapping [-180°, 180°)
inline constexpr struct east final :
    mp_units::absolute_point_origin<geometric_azimuth,
                                    mp_units::wrap_to_range{-180 * mp_units::si::degree, 180 * mp_units::si::degree}> {
} east;

// Bearing: 0° = North, clockwise positive
// Axis inversion relative to geometric_azimuth: bearing = 90° − azimuth (sign flip, not a shift).
// frame_projection<east, north_cw> and frame_projection<north_cw, east> connect the two frames,
// so .point_for(north_cw) / .point_for(east) work across the inversion.
// Because north_ccw is a relative_point_origin rooted at east, bearing ↔ heading also works
// automatically: point_for(north_ccw) projects to east first, then walks down.
inline constexpr struct north_cw final :
    mp_units::absolute_point_origin<geo_bearing,
                                    mp_units::wrap_to_range{-180 * mp_units::si::degree, 180 * mp_units::si::degree}> {
} north_cw;

// Heading azimuth: 0° = North, counter-clockwise positive (heading = geometric_azimuth - 90°)
// Implemented as a relative origin: offset -90° from east
inline constexpr struct north_ccw final :
    mp_units::relative_point_origin<east - 90.0 * mp_units::si::degree,
                                    mp_units::wrap_to_range{-180 * mp_units::si::degree, 180 * mp_units::si::degree}> {
} north_ccw;

template<typename T = double>
using latitude = mp_units::quantity_point<geo_latitude[mp_units::si::degree], equator, T>;

template<typename T = double>
using longitude = mp_units::quantity_point<geo_longitude[mp_units::si::degree], prime_meridian, T>;

template<typename T = double>
using elevation = mp_units::quantity_point<geo_elevation[mp_units::si::degree], horizon, T>;

template<typename T = double>
using azimuth = mp_units::quantity_point<geometric_azimuth[mp_units::si::degree], east, T>;

template<typename T = double>
using bearing = mp_units::quantity_point<geo_bearing[mp_units::si::degree], north_cw, T>;

template<typename T = double>
using heading = mp_units::quantity_point<heading_azimuth[mp_units::si::degree], north_ccw, T>;

template<class CharT, class Traits, typename T>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const latitude<T>& lat)
{
  const auto& q = lat.quantity_ref_from(geographic::equator);
  return (is_gteq_zero(q)) ? (os << q << " N") : (os << -q << " S");
}

template<class CharT, class Traits, typename T>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const longitude<T>& lon)
{
  const auto& q = lon.quantity_ref_from(geographic::prime_meridian);
  return (is_gteq_zero(q)) ? (os << q << " E") : (os << -q << " W");
}

template<class CharT, class Traits, typename T>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const elevation<T>& elev)
{
  return os << elev.quantity_ref_from(geographic::horizon);
}

template<class CharT, class Traits, typename T>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const azimuth<T>& az)
{
  return os << "Az " << az.quantity_ref_from(geographic::east);
}

template<class CharT, class Traits, typename T>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const bearing<T>& brg)
{
  return os << "BRG " << brg.quantity_ref_from(geographic::north_cw);
}

template<class CharT, class Traits, typename T>
std::basic_ostream<CharT, Traits>& operator<<(std::basic_ostream<CharT, Traits>& os, const heading<T>& hdg)
{
  return os << "HDG " << hdg.quantity_ref_from(geographic::north_ccw);
}

inline namespace literals {

constexpr latitude<double> operator""_N(long double v)
{
  return equator + static_cast<double>(v) * geo_latitude[mp_units::si::degree];
}

constexpr latitude<double> operator""_S(long double v)
{
  return equator - static_cast<double>(v) * geo_latitude[mp_units::si::degree];
}

constexpr longitude<double> operator""_E(long double v)
{
  return prime_meridian + static_cast<double>(v) * geo_longitude[mp_units::si::degree];
}

constexpr longitude<double> operator""_W(long double v)
{
  return prime_meridian - static_cast<double>(v) * geo_longitude[mp_units::si::degree];
}

}  // namespace literals

}  // namespace geographic

// Axis-inversion projections between geometric_azimuth (east, E/CCW+) and bearing (north_cw, N/CW+).
// bearing = 90° − azimuth  (self-inverse formula).
// Explicit specializations must live outside namespace geographic (in namespace mp_units or at
// global scope) because they specialize a template defined in namespace mp_units.
template<>
inline constexpr auto mp_units::frame_projection<geographic::east, geographic::north_cw> =
  [](mp_units::QuantityPointOf<geographic::geometric_azimuth> auto qp) constexpr {
    const auto az = mp_units::isq::angular_measure(qp.quantity_from(geographic::east));
    return geographic::north_cw + geographic::geo_bearing(90.0 * mp_units::si::degree - az);
  };

template<>
inline constexpr auto mp_units::frame_projection<geographic::north_cw, geographic::east> =
  [](mp_units::QuantityPointOf<geographic::geo_bearing> auto qp) constexpr {
    const auto brg = mp_units::isq::angular_measure(qp.quantity_from(geographic::north_cw));
    return geographic::east + geographic::geometric_azimuth(90.0 * mp_units::si::degree - brg);
  };

// Note: No std::numeric_limits specializations needed!
// The generic specialization in quantity_point.h automatically handles all bounded quantity_points
// by querying the bounds from the origin's NTTP parameter.

template<typename T, typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::latitude<T>, Char> :
    formatter<typename geographic::latitude<T>::quantity_type, Char> {
  template<typename FormatContext>
  auto format(geographic::latitude<T> lat, FormatContext& ctx) const -> decltype(ctx.out())
  {
    const auto& q = lat.quantity_ref_from(geographic::equator);
    ctx.advance_to(formatter<typename geographic::latitude<T>::quantity_type, Char>::format(q >= 0 ? q : -q, ctx));
    return MP_UNITS_STD_FMT::format_to(ctx.out(), "{}", q >= 0 ? " N" : " S");
  }
};

template<typename T, typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::longitude<T>, Char> :
    formatter<typename geographic::longitude<T>::quantity_type, Char> {
  template<typename FormatContext>
  auto format(geographic::longitude<T> lon, FormatContext& ctx) const -> decltype(ctx.out())
  {
    const auto& q = lon.quantity_ref_from(geographic::prime_meridian);
    ctx.advance_to(formatter<typename geographic::longitude<T>::quantity_type, Char>::format(q >= 0 ? q : -q, ctx));
    return MP_UNITS_STD_FMT::format_to(ctx.out(), "{}", q >= 0 ? " E" : " W");
  }
};

template<typename T, typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::elevation<T>, Char> :
    formatter<typename geographic::elevation<T>::quantity_type, Char> {
  template<typename FormatContext>
  auto format(geographic::elevation<T> elev, FormatContext& ctx) const -> decltype(ctx.out())
  {
    return formatter<typename geographic::elevation<T>::quantity_type, Char>::format(
      elev.quantity_ref_from(geographic::horizon), ctx);
  }
};

template<typename T, typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::azimuth<T>, Char> :
    formatter<typename geographic::azimuth<T>::quantity_type, Char> {
  template<typename FormatContext>
  auto format(geographic::azimuth<T> az, FormatContext& ctx) const -> decltype(ctx.out())
  {
    ctx.advance_to(MP_UNITS_STD_FMT::format_to(ctx.out(), "Az "));
    return formatter<typename geographic::azimuth<T>::quantity_type, Char>::format(
      az.quantity_ref_from(geographic::east), ctx);
  }
};

template<typename T, typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::bearing<T>, Char> :
    formatter<typename geographic::bearing<T>::quantity_type, Char> {
  template<typename FormatContext>
  auto format(geographic::bearing<T> brg, FormatContext& ctx) const -> decltype(ctx.out())
  {
    ctx.advance_to(MP_UNITS_STD_FMT::format_to(ctx.out(), "BRG "));
    return formatter<typename geographic::bearing<T>::quantity_type, Char>::format(
      brg.quantity_ref_from(geographic::north_cw), ctx);
  }
};

template<typename T, typename Char>
struct MP_UNITS_STD_FMT::formatter<geographic::heading<T>, Char> :
    formatter<typename geographic::heading<T>::quantity_type, Char> {
  template<typename FormatContext>
  auto format(geographic::heading<T> hdg, FormatContext& ctx) const -> decltype(ctx.out())
  {
    ctx.advance_to(MP_UNITS_STD_FMT::format_to(ctx.out(), "HDG "));
    return formatter<typename geographic::heading<T>::quantity_type, Char>::format(
      hdg.quantity_ref_from(geographic::north_ccw), ctx);
  }
};

namespace geographic {

using distance = mp_units::quantity<mp_units::isq::distance[mp_units::si::kilo<mp_units::si::metre>]>;

// A geographic position couples latitude and longitude: reflecting latitude at a
// pole requires shifting longitude by 180°. Because this constraint spans both
// axes, it cannot be expressed by a single-axis policy on `equator`; instead the
// constructor performs the coupled normalization, leaving `prime_meridian`'s
// `wrap_to_range` to handle the resulting longitude wrap.
template<typename T>
class position {
public:
  latitude<T> lat;
  longitude<T> lon;

  // NOLINTNEXTLINE(bugprone-easily-swappable-parameters)
  constexpr position(latitude<T> lat_in, longitude<T> lon_in) noexcept : lon(lon_in)
  {
    using mp_units::quantity;
    using mp_units::si::degree;

    constexpr quantity half_turn = T{180} * degree;
    constexpr quantity full_turn = T{360} * degree;
    constexpr quantity quarter_turn = T{90} * degree;

    // Latitude reflection (`half_turn - lat_q`) is not defined on points, so the
    // normalization is done in displacement space relative to the equator.
    quantity lat_q = lat_in.quantity_from(equator);

    // Fold latitude into [-180°, 180°] first.
    lat_q = fmod(lat_q + half_turn, full_turn);
    if (lat_q < lat_q.zero()) lat_q += full_turn;
    lat_q -= half_turn;

    // Reflect at the poles, shifting longitude by 180°; wrap_to_range on
    // prime_meridian normalizes the longitude back into (-180°, 180°].
    if (lat_q > quarter_turn) {
      lat_q = half_turn - lat_q;
      lon += half_turn;
    } else if (lat_q < -quarter_turn) {
      lat_q = -half_turn - lat_q;
      lon += half_turn;
    }

    lat = equator + lat_q;
  }

  // NOLINTNEXTLINE(bugprone-easily-swappable-parameters)
  friend distance spherical_distance(position from, position to)
  {
    using namespace mp_units;
    constexpr quantity earth_radius = 6'371 * isq::radius[si::kilo<si::metre>];

    using si::sin, si::cos, si::asin, si::acos;

    const quantity from_lat = isq::angular_measure(from.lat.quantity_ref_from(equator));
    const quantity from_lon = isq::angular_measure(from.lon.quantity_ref_from(prime_meridian));
    const quantity to_lat = isq::angular_measure(to.lat.quantity_ref_from(equator));
    const quantity to_lon = isq::angular_measure(to.lon.quantity_ref_from(prime_meridian));

    // https://en.wikipedia.org/wiki/Great-circle_distance#Formulae
    if constexpr (sizeof(T) >= 8) {
      // spherical law of cosines
      const quantity central_angle =
        acos(sin(from_lat) * sin(to_lat) + cos(from_lat) * cos(to_lat) * cos(to_lon - from_lon));
      // const auto central_angle = 2 * asin(sqrt(0.5 - cos(to_lat - from_lat) / 2 + cos(from_lat) * cos(to_lat) * (1
      // - cos(lon2_rad - from_lon)) / 2));

      return quantity_cast<isq::distance>((earth_radius * central_angle).in(earth_radius.unit));
    } else {
      // the haversine formula
      const quantity sin_lat = sin((to_lat - from_lat) / 2);
      const quantity sin_lon = sin((to_lon - from_lon) / 2);
      const quantity central_angle =
        2 * asin(sqrt(sin_lat * sin_lat + cos(from_lat) * cos(to_lat) * sin_lon * sin_lon));

      return quantity_cast<isq::distance>((earth_radius * central_angle).in(earth_radius.unit));
    }
  }
};

}  // namespace geographic

// An example of a really simplified tactical glide computer
// Simplifications:
// - glider 100% clean and with full factory performance (brand new painting)
// - no influence of the ballast (pilot weight, water, etc) to glider performance
// - only one point on a glider polar curve
// - no influence of bank angle (during circling) on a glider performance
// - no wind
// - constant thermals strength
// - thermals exactly where and when we need them ;-)
// - no airspaces
// - ground level changes linearly between waypoints
// - no ground obstacles (e.g. mountains) to pass
// - flight path exactly on a shortest possible line to destination

namespace glide_computer {

// https://en.wikipedia.org/wiki/Flight_planning#Units_of_measurement
QUANTITY_SPEC(rate_of_climb_speed, mp_units::isq::speed, mp_units::isq::height / mp_units::isq::duration);

// length
using distance = mp_units::quantity<mp_units::isq::distance[mp_units::si::kilo<mp_units::si::metre>]>;
// TODO change to isq::height in V3
using height = mp_units::quantity<mp_units::isq::altitude[mp_units::si::metre]>;

// time
using duration = mp_units::quantity<mp_units::isq::duration[mp_units::si::second]>;
using timestamp = mp_units::quantity_point<mp_units::isq::time[mp_units::si::second],
                                           mp_units::chrono_point_origin<std::chrono::system_clock>>;

// speed
using velocity = mp_units::quantity<mp_units::isq::speed[mp_units::si::kilo<mp_units::si::metre> / mp_units::si::hour]>;
using rate_of_climb = mp_units::quantity<rate_of_climb_speed[mp_units::si::metre / mp_units::si::second]>;

// definition of glide computer databases and utilities
struct glider {
  struct polar_point {
    velocity v;
    rate_of_climb climb;
  };

  std::string name;
  std::array<polar_point, 1> polar;
};

constexpr mp_units::QuantityOf<mp_units::dimensionless> auto glide_ratio(const glider::polar_point& polar)
{
  return polar.v / -polar.climb;
}

struct weather {
  height cloud_base;
  rate_of_climb thermal_strength;
};

struct waypoint {
  std::string name;
  geographic::position<double> pos;
  geographic::msl_altitude alt;
};

class task {
public:
  using waypoints = std::vector<waypoint>;

  class leg {
    const waypoint* begin_;
    const waypoint* end_;
    distance length_ = spherical_distance(begin().pos, end().pos);
  public:
    // NOLINTNEXTLINE(bugprone-easily-swappable-parameters)
    leg(const waypoint& b, const waypoint& e) noexcept : begin_(&b), end_(&e) {}
    [[nodiscard]] constexpr const waypoint& begin() const { return *begin_; };
    [[nodiscard]] constexpr const waypoint& end() const { return *end_; }
    [[nodiscard]] constexpr distance get_distance() const { return length_; }
  };
  using legs = std::vector<leg>;

  template<std::ranges::input_range R>
    requires std::same_as<std::ranges::range_value_t<R>, waypoint>
  explicit task(const R& r) : waypoints_(std::ranges::begin(r), std::ranges::end(r))
  {
  }

  task(std::initializer_list<waypoint> wpts) : waypoints_(wpts) {}

  [[nodiscard]] const waypoints& get_waypoints() const { return waypoints_; }
  [[nodiscard]] const legs& get_legs() const { return legs_; }

  [[nodiscard]] const waypoint& get_start() const { return waypoints_.front(); }
  [[nodiscard]] const waypoint& get_finish() const { return waypoints_.back(); }

  [[nodiscard]] distance get_distance() const { return length_; }

  [[nodiscard]] distance get_leg_dist_offset(std::size_t leg_index) const
  {
    return leg_index == 0 ? distance{} : leg_total_distances_[leg_index - 1];
  }
  [[nodiscard]] std::size_t get_leg_index(distance dist) const
  {
    return static_cast<std::size_t>(
      std::ranges::distance(leg_total_distances_.cbegin(), std::ranges::lower_bound(leg_total_distances_, dist)));
  }

private:
  waypoints waypoints_;
  legs legs_ = make_legs(waypoints_);
  std::vector<distance> leg_total_distances_ = make_leg_total_distances(legs_);
  distance length_ = leg_total_distances_.back();

  static legs make_legs(const task::waypoints& wpts);
  static std::vector<distance> make_leg_total_distances(const legs& legs);
};

struct safety {
  height min_agl_height;
};

struct aircraft_tow {
  height height_agl;
  rate_of_climb performance;
};

struct flight_point {
  timestamp ts;
  geographic::msl_altitude alt;
  std::size_t leg_idx = 0;
  distance dist{};
};

geographic::msl_altitude terrain_level_alt(const task& t, const flight_point& pos);

constexpr height agl(geographic::msl_altitude glider_alt, geographic::msl_altitude terrain_level)
{
  return glider_alt - terrain_level;
}

inline mp_units::quantity<mp_units::isq::length[mp_units::si::kilo<mp_units::si::metre>]> length_3d(distance dist,
                                                                                                    height h)
{
  return hypot(dist, h);
}

distance glide_distance(const flight_point& pos, const glider& g, const task& t, const safety& s,
                        geographic::msl_altitude ground_alt);

void estimate(timestamp start_ts, const glider& g, const weather& w, const task& t, const safety& s,
              const aircraft_tow& at);

}  // namespace glide_computer

namespace glide_computer {

using namespace mp_units;

task::legs task::make_legs(const waypoints& wpts)
{
  task::legs res;
  res.reserve(wpts.size() - 1);
  auto to_leg = [](const waypoint& w1, const waypoint& w2) { return task::leg(w1, w2); };
  std::ranges::transform(wpts.cbegin(), prev(wpts.cend()), next(wpts.cbegin()), wpts.cend(), std::back_inserter(res),
                         to_leg);
  return res;
}

std::vector<distance> task::make_leg_total_distances(const legs& legs)
{
  std::vector<distance> res;
  res.reserve(legs.size());
  auto to_length = [](const leg& l) { return l.get_distance(); };
  std::transform_inclusive_scan(legs.cbegin(), legs.cend(), std::back_inserter(res), std::plus(), to_length,
                                distance::zero());
  return res;
}

geographic::msl_altitude terrain_level_alt(const task& t, const flight_point& pos)
{
  const task::leg& l = t.get_legs()[pos.leg_idx];
  const height alt_diff = l.end().alt - l.begin().alt;
  return l.begin().alt + alt_diff * ((pos.dist - t.get_leg_dist_offset(pos.leg_idx)) / l.get_distance());
}

// Returns `x` of the intersection of a glide line and a terrain line.
// y = -x / glide_ratio + pos.alt;
// y = (finish_alt - ground_alt) / dist_to_finish * x + ground_alt + min_agl_height;
distance glide_distance(const flight_point& pos, const glider& g, const task& t, const safety& s,
                        geographic::msl_altitude ground_alt)
{
  const auto dist_to_finish = t.get_distance() - pos.dist;
  return quantity_cast<isq::distance>(ground_alt + s.min_agl_height - pos.alt) /
         ((ground_alt - t.get_finish().alt) / dist_to_finish - 1 / glide_ratio(g.polar[0]));
}

}  // namespace glide_computer

namespace {

using namespace glide_computer;

void print(std::string_view phase_name, timestamp start_ts, const glide_computer::flight_point& point,
           const glide_computer::flight_point& new_point)
{
  std::cout << MP_UNITS_STD_FMT::format(
    "| {:<12} | {:>9:N[.1f]} (Total: {:>9:N[.1f]}) | {:>8:N[.1f]} (Total: {:>8:N[.1f]}) | {:>7:N[.0f]} ({:>6:N[.0f]}) "
    "|\n",
    phase_name, value_cast<si::minute>(new_point.ts - point.ts), value_cast<si::minute>(new_point.ts - start_ts),
    new_point.dist - point.dist, new_point.dist, new_point.alt - point.alt, new_point.alt);
}

flight_point takeoff(timestamp start_ts, const task& t) { return {start_ts, t.get_start().alt}; }

flight_point tow(timestamp start_ts, const flight_point& pos, const aircraft_tow& at)
{
  const duration d = (at.height_agl / at.performance);
  const flight_point new_pos{pos.ts + d, pos.alt + at.height_agl, pos.leg_idx, pos.dist};

  print("Tow", start_ts, pos, new_pos);
  return new_pos;
}

flight_point circle(timestamp start_ts, const flight_point& pos, const glider& g, const weather& w, const task& t,
                    height& height_to_gain)
{
  const height h_agl = agl(pos.alt, terrain_level_alt(t, pos));
  const height circling_height = std::min(w.cloud_base - h_agl, height_to_gain);
  const rate_of_climb circling_rate = w.thermal_strength + g.polar[0].climb;
  const duration d = (circling_height / circling_rate);
  const flight_point new_pos{pos.ts + d, pos.alt + circling_height, pos.leg_idx, pos.dist};

  height_to_gain -= circling_height;

  print("Circle", start_ts, pos, new_pos);
  return new_pos;
}

flight_point glide(timestamp start_ts, const flight_point& pos, const glider& g, const task& t, const safety& s)
{
  const auto ground_alt = terrain_level_alt(t, pos);
  const auto dist = glide_distance(pos, g, t, s, ground_alt);
  const auto new_distance = pos.dist + dist;
  const auto alt = ground_alt + s.min_agl_height;
  const auto l3d = length_3d(dist, pos.alt - alt);
  const duration d = l3d / g.polar[0].v;
  const flight_point new_pos{pos.ts + d, terrain_level_alt(t, pos) + s.min_agl_height, t.get_leg_index(new_distance),
                             new_distance};

  print("Glide", start_ts, pos, new_pos);
  return new_pos;
}

flight_point final_glide(timestamp start_ts, const flight_point& pos, const glider& g, const task& t)
{
  const auto dist = t.get_distance() - pos.dist;
  const auto l3d = length_3d(dist, pos.alt - t.get_finish().alt);
  const duration d = l3d / g.polar[0].v;
  const flight_point new_pos{pos.ts + d, t.get_finish().alt, t.get_legs().size() - 1, pos.dist + dist};

  print("Final Glide", start_ts, pos, new_pos);
  return new_pos;
}

}  // namespace

namespace glide_computer {

void estimate(timestamp start_ts, const glider& g, const weather& w, const task& t, const safety& s,
              const aircraft_tow& at)
{
  std::cout << MP_UNITS_STD_FMT::format("| {:<12} | {:^28} | {:^26} | {:^21} |\n", "Flight phase", "Duration",
                                        "Distance", "Height");
  std::cout << MP_UNITS_STD_FMT::format("|{0:-^14}|{0:-^30}|{0:-^28}|{0:-^23}|\n", "");

  // ready to takeoff
  flight_point pos = takeoff(start_ts, t);

  // estimate aircraft towing
  pos = tow(start_ts, pos, at);

  // estimate the msl_altitude needed to reach the finish line from this place
  const geographic::msl_altitude final_glide_alt =
    t.get_finish().alt + quantity_cast<isq::height>(t.get_distance() / glide_ratio(g.polar[0]));

  // how much height we still need to gain in the thermalls to reach the destination?
  height height_to_gain = final_glide_alt - pos.alt;

  do {
    // glide to the next thermall
    pos = glide(start_ts, pos, g, t, s);

    // circle in a thermall to gain height
    pos = circle(start_ts, pos, g, w, t, height_to_gain);
  } while (height_to_gain > height{});

  // final glide
  pos = final_glide(start_ts, pos, g, t);
}

}  // namespace glide_computer

namespace {

using namespace geographic;
using namespace glide_computer;
using namespace mp_units;

auto get_gliders()
{
  using namespace mp_units::si::unit_symbols;
  MP_UNITS_DIAGNOSTIC_PUSH
  MP_UNITS_DIAGNOSTIC_IGNORE_MISSING_BRACES
  static const std::array gliders = {glider{"SZD-30 Pirat", {83 * km / h, -0.7389 * m / s}},
                                     glider{"SZD-51 Junior", {80 * km / h, -0.6349 * m / s}},
                                     glider{"SZD-48 Jantar Std 3", {110 * km / h, -0.77355 * m / s}},
                                     glider{"SZD-56 Diana", {110 * km / h, -0.63657 * m / s}}};
  MP_UNITS_DIAGNOSTIC_POP
  return gliders;
}

auto get_weather_conditions()
{
  using namespace mp_units::si::unit_symbols;
  static const std::array weather_conditions = {std::pair{"Good", weather{1900 * m, 4.3 * m / s}},
                                                std::pair{"Medium", weather{1550 * m, 2.8 * m / s}},
                                                std::pair{"Bad", weather{850 * m, 1.8 * m / s}}};
  return weather_conditions;
}

auto get_waypoints()
{
  using namespace geographic::literals;
  using namespace mp_units::yard_pound::unit_symbols;
  static const std::array waypoints = {
    waypoint{"EPPR", {54.24772_N, 18.6745_E}, mean_sea_level + 16. * ft},   // N54°14'51.8" E18°40'28.2"
    waypoint{"EPGI", {53.52442_N, 18.84947_E}, mean_sea_level + 115. * ft}  // N53°31'27.9" E18°50'58.1"
  };
  return waypoints;
}

template<std::ranges::input_range R>
  requires(std::same_as<std::ranges::range_value_t<R>, glider>)
void print(const R& gliders)
{
  std::cout << "Gliders:\n";
  std::cout << "========\n";
  for (const auto& g : gliders) {
    std::cout << "- Name: " << g.name << "\n";
    std::cout << "- Polar:\n";
    for (const auto& p : g.polar) {
      const auto ratio = glide_ratio(g.polar[0]).in(one);
      std::cout << MP_UNITS_STD_FMT::format("  * {::N[.4f]} @ {::N[.1f]} -> {::N[.1f]} ({::N[.1f]})\n", p.climb, p.v,
                                            ratio, si::asin(1 / ratio).in(si::degree));
    }
    std::cout << "\n";
  }
}

template<std::ranges::input_range R>
  requires(std::same_as<std::ranges::range_value_t<R>, std::pair<const char*, weather>>)
void print(const R& conditions)
{
  std::cout << "Weather:\n";
  std::cout << "========\n";
  for (const auto& c : conditions) {
    std::cout << "- " << c.first << "\n";
    const auto& w = c.second;
    std::cout << "  * Cloud base:        " << MP_UNITS_STD_FMT::format("{::N[.0f]}", w.cloud_base) << " AGL\n";
    std::cout << "  * Thermals strength: " << MP_UNITS_STD_FMT::format("{::N[.1f]}", w.thermal_strength) << "\n";
    std::cout << "\n";
  }
}

template<std::ranges::input_range R>
  requires(std::same_as<std::ranges::range_value_t<R>, waypoint>)
void print(const R& waypoints)
{
  std::cout << "Waypoints:\n";
  std::cout << "==========\n";
  for (const auto& w : waypoints)
    std::cout << MP_UNITS_STD_FMT::format("- {}: {} {}, {::N[.1f]}\n", w.name, w.pos.lat, w.pos.lon, w.alt);
  std::cout << "\n";
}

void print(const task& t)
{
  std::cout << "Task:\n";
  std::cout << "=====\n";

  std::cout << "- Start: " << t.get_start().name << "\n";
  std::cout << "- Finish: " << t.get_finish().name << "\n";
  std::cout << "- Length:  " << MP_UNITS_STD_FMT::format("{::N[.1f]}", t.get_distance()) << "\n";

  std::cout << "- Legs: "
            << "\n";
  for (const auto& l : t.get_legs())
    std::cout << MP_UNITS_STD_FMT::format("  * {} -> {} ({::N[.1f]})\n", l.begin().name, l.end().name,
                                          l.get_distance());
  std::cout << "\n";
}

void print(const safety& s)
{
  std::cout << "Safety:\n";
  std::cout << "=======\n";
  std::cout << "- Min AGL separation: " << MP_UNITS_STD_FMT::format("{::N[.0f]}", s.min_agl_height) << "\n";
  std::cout << "\n";
}

void print(const aircraft_tow& tow)
{
  std::cout << "Tow:\n";
  std::cout << "====\n";
  std::cout << "- Type:        aircraft\n";
  std::cout << "- Height:      " << MP_UNITS_STD_FMT::format("{::N[.0f]}", tow.height_agl) << "\n";
  std::cout << "- Performance: " << MP_UNITS_STD_FMT::format("{::N[.1f]}", tow.performance) << "\n";
  std::cout << "\n";
}

void example()
{
  using mp_units::si::unit_symbols::m;
  using mp_units::si::unit_symbols::s;

  const safety sfty = {300 * m};
  const auto gliders = get_gliders();
  const auto waypoints = get_waypoints();
  const auto weather_conditions = get_weather_conditions();
  const task t = {waypoints[0], waypoints[1], waypoints[0]};
  const aircraft_tow tow = {400 * m, 1.6 * m / s};
  const timestamp start_time(std::chrono::system_clock::now());

  print(sfty);
  print(gliders);
  print(waypoints);
  print(weather_conditions);
  print(t);
  print(tow);

  for (const auto& g : gliders) {
    for (const auto& c : weather_conditions) {
      const std::string txt = "Scenario: Glider = " + g.name + ", Weather = " + c.first;
      std::cout << txt << "\n";
      std::cout << MP_UNITS_STD_FMT::format("{0:=^{1}}\n\n", "", txt.size());

      estimate(start_time, g, c.second, t, sfty, tow);

      std::cout << "\n\n";
    }
  }
}

}  // namespace

int main()
{
  try {
    example();
  } catch (const std::exception& ex) {
    std::cerr << "Unhandled std exception caught: " << ex.what() << '\n';
  } catch (...) {
    std::cerr << "Unhandled unknown exception caught\n";
  }
}
