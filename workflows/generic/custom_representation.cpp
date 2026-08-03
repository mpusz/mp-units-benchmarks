// A user-defined representation type, which is what the representation concepts actually exist for.
// Every other workflow stores `double`, so the concept machinery - RepresentationOf, the scaling
// paths, the customization points - is only ever asked the easy question. Here it has to inspect a
// class type: its value_type, its operators, whether it scales by magnitude at all.
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

namespace lab {

// A measured value carrying its uncertainty - the classic reason to reach for a custom
// representation rather than a plain arithmetic type.
template<typename T>
class measured {
  T value_{};
  T uncertainty_{};

public:
  using value_type = T;

  measured() = default;
  constexpr explicit measured(T v, T u = T{}) : value_(v), uncertainty_(u) {}

  [[nodiscard]] constexpr T value() const { return value_; }
  [[nodiscard]] constexpr T uncertainty() const { return uncertainty_; }

  [[nodiscard]] constexpr measured operator-() const { return measured{-value_, uncertainty_}; }

  [[nodiscard]] friend constexpr measured operator+(const measured& l, const measured& r)
  {
    return measured{l.value_ + r.value_, l.uncertainty_ + r.uncertainty_};
  }
  [[nodiscard]] friend constexpr measured operator-(const measured& l, const measured& r)
  {
    return measured{l.value_ - r.value_, l.uncertainty_ + r.uncertainty_};
  }
  [[nodiscard]] friend constexpr measured operator*(const measured& l, const measured& r)
  {
    return measured{l.value_ * r.value_, l.uncertainty_ * r.value_ + r.uncertainty_ * l.value_};
  }
  [[nodiscard]] friend constexpr measured operator/(const measured& l, const measured& r)
  {
    return measured{l.value_ / r.value_, (l.uncertainty_ + r.uncertainty_) / r.value_};
  }
  [[nodiscard]] friend constexpr measured operator*(const measured& v, T f) { return measured{v.value_ * f, v.uncertainty_ * f}; }
  [[nodiscard]] friend constexpr measured operator*(T f, const measured& v) { return measured{f * v.value_, f * v.uncertainty_}; }
  [[nodiscard]] friend constexpr measured operator/(const measured& v, T f) { return measured{v.value_ / f, v.uncertainty_ / f}; }

  [[nodiscard]] friend constexpr bool operator==(const measured&, const measured&) = default;
  [[nodiscard]] friend constexpr auto operator<=>(const measured& l, const measured& r) { return l.value_ <=> r.value_; }
};

}  // namespace lab

template<typename T>
constexpr bool mp_units::is_scalar<lab::measured<T>> = true;
template<typename T>
constexpr bool mp_units::treat_as_floating_point<lab::measured<T>> = mp_units::treat_as_floating_point<T>;

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;
  using m_t = lab::measured<double>;

  const quantity length = m_t{2.415, 0.002} * isq::length[m];
  const quantity width = m_t{1.203, 0.001} * isq::width[m];
  const quantity duration = m_t{4.5, 0.05} * isq::duration[s];

  const quantity area = length * width;
  const quantity speed = length / duration;
  const quantity converted = length.in(si::milli<si::metre>);  // scaling a class-type representation

  std::printf("%f+-%f %f %f\n", area.numerical_value_in(m2).value(), area.numerical_value_in(m2).uncertainty(),
              speed.numerical_value_in(m / s).value(), converted.numerical_value_in(mm).value());
}
