package kr.ac.khu.padosat.fds;

import java.util.ArrayList;
import java.util.List;

import org.orekit.bodies.GeodeticPoint;
import org.orekit.bodies.OneAxisEllipsoid;
import org.orekit.frames.FramesFactory;
import org.orekit.frames.TopocentricFrame;
import org.orekit.propagation.BoundedPropagator;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.Constants;
import org.orekit.utils.IERSConventions;
import org.orekit.utils.PVCoordinates;

/** Pass boundaries are bracketed on a cadence grid and refined to below 0.1 s. */
public final class PassPredictor {
    public record Observation(double azimuthDeg, double elevationDeg, double rangeM,
                              double rangeRateMps) { }
    public record Pass(String id, FdsConfig.Station station, AbsoluteDate aos,
                       AbsoluteDate los, AbsoluteDate peak, double maxElevationDeg,
                       boolean clippedStart, boolean clippedEnd) { }

    private final BoundedPropagator orbit;
    private final FdsConfig.Station station;
    private final TopocentricFrame topocentric;

    public PassPredictor(BoundedPropagator orbit, FdsConfig.Station station) {
        this.orbit = orbit;
        this.station = station;
        OneAxisEllipsoid earth = new OneAxisEllipsoid(Constants.WGS84_EARTH_EQUATORIAL_RADIUS,
                Constants.WGS84_EARTH_FLATTENING,
                FramesFactory.getITRF(IERSConventions.IERS_2010, true));
        GeodeticPoint site = new GeodeticPoint(Math.toRadians(station.latitudeDeg()),
                Math.toRadians(station.longitudeDeg()), station.altitudeM());
        this.topocentric = new TopocentricFrame(earth, site, station.id());
    }

    public Observation at(AbsoluteDate time) {
        PVCoordinates pv = orbit.getPVCoordinates(time, FramesFactory.getGCRF());
        double azimuth = Math.toDegrees(topocentric.getAzimuth(
                pv.getPosition(), FramesFactory.getGCRF(), time));
        double elevation = Math.toDegrees(topocentric.getElevation(
                pv.getPosition(), FramesFactory.getGCRF(), time));
        double range = topocentric.getRange(pv.getPosition(), FramesFactory.getGCRF(), time);
        double rate = topocentric.getRangeRate(pv, FramesFactory.getGCRF(), time);
        return new Observation(azimuth, elevation, range, rate);
    }

    public List<Pass> predict(AbsoluteDate start, AbsoluteDate end, int scanStepSeconds) {
        List<Pass> passes = new ArrayList<>();
        AbsoluteDate previous = start;
        boolean visible = at(start).elevationDeg() >= station.minimumElevationDeg();
        AbsoluteDate aos = visible ? start : null;
        boolean clippedStart = visible;
        while (previous.compareTo(end) < 0) {
            AbsoluteDate next = previous.shiftedBy(Math.min(scanStepSeconds, end.durationFrom(previous)));
            boolean nextVisible = at(next).elevationDeg() >= station.minimumElevationDeg();
            if (visible != nextVisible) {
                AbsoluteDate crossing = crossing(previous, next, visible);
                if (nextVisible) {
                    aos = crossing;
                    clippedStart = false;
                } else {
                    passes.add(makePass(passes.size() + 1, aos, crossing, clippedStart, false));
                    aos = null;
                }
            }
            previous = next;
            visible = nextVisible;
        }
        if (visible) {
            passes.add(makePass(passes.size() + 1, aos, end, clippedStart, true));
        }
        return passes;
    }

    private AbsoluteDate crossing(AbsoluteDate left, AbsoluteDate right, boolean leftVisible) {
        while (right.durationFrom(left) > 0.1) {
            AbsoluteDate middle = left.shiftedBy(right.durationFrom(left) / 2.0);
            boolean middleVisible = at(middle).elevationDeg() >= station.minimumElevationDeg();
            if (middleVisible == leftVisible) {
                left = middle;
            } else {
                right = middle;
            }
        }
        return left.shiftedBy(right.durationFrom(left) / 2.0);
    }

    private Pass makePass(int sequence, AbsoluteDate aos, AbsoluteDate los,
                          boolean clippedStart, boolean clippedEnd) {
        AbsoluteDate peak = peak(aos, los);
        return new Pass(station.id() + "-" + String.format("%03d", sequence), station,
                aos, los, peak, at(peak).elevationDeg(), clippedStart, clippedEnd);
    }

    private AbsoluteDate peak(AbsoluteDate aos, AbsoluteDate los) {
        AbsoluteDate left = aos;
        AbsoluteDate right = los;
        for (int count = 0; count < 36 && right.durationFrom(left) > 0.1; count++) {
            double span = right.durationFrom(left);
            AbsoluteDate first = left.shiftedBy(span * 0.38196601125);
            AbsoluteDate second = left.shiftedBy(span * 0.61803398875);
            if (at(first).elevationDeg() < at(second).elevationDeg()) {
                left = first;
            } else {
                right = second;
            }
        }
        return left.shiftedBy(right.durationFrom(left) / 2.0);
    }
}
