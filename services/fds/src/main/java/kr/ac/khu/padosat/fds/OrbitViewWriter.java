package kr.ac.khu.padosat.fds;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Locale;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.bodies.CelestialBodyFactory;
import org.orekit.bodies.GeodeticPoint;
import org.orekit.bodies.OneAxisEllipsoid;
import org.orekit.frames.FramesFactory;
import org.orekit.propagation.BoundedPropagator;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.Constants;
import org.orekit.utils.IERSConventions;

/** Read-only display product derived from the same validated FDS propagation. */
public final class OrbitViewWriter {
    private OrbitViewWriter() { }

    public static void write(Path file, FdsConfig config, OrbitInput.Selected source,
                             Path input, BoundedPropagator orbit,
                             AbsoluteDate start, AbsoluteDate end) throws IOException {
        var itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, true);
        OneAxisEllipsoid earth = new OneAxisEllipsoid(Constants.WGS84_EARTH_EQUATORIAL_RADIUS,
                Constants.WGS84_EARTH_FLATTENING, itrf);
        double period = orbit.propagate(start).getOrbit().getKeplerianPeriod();
        if (!Double.isFinite(period) || period <= 0.0) {
            throw new IllegalArgumentException("orbit period unavailable for viewer");
        }
        try (BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
            writer.write("{\"schema\":\"fds.orbit-view.v1\",\"quality\":\"PRELIMINARY_NOT_FOR_OPERATIONS\"");
            writer.write(String.format(Locale.ROOT,
                    ",\"norad_id\":%d,\"source\":\"%s\",\"model\":\"%s\",\"input_epoch_utc\":\"%s\""
                            + ",\"input_sha256\":\"%s\",\"generated_utc\":\"%s\""
                            + ",\"start_utc\":\"%s\",\"end_utc\":\"%s\""
                            + ",\"sample_step_s\":%d,\"orbital_period_s\":%.3f",
                    config.noradId(), source.source(), source.model(), FdsOutput.utc(source.epoch()),
                    FdsOutput.sha256(input), Instant.now(), FdsOutput.utc(start), FdsOutput.utc(end),
                    config.ephemerisStepSeconds(), period));
            writer.write(",\"stations\":[");
            boolean firstStation = true;
            for (FdsConfig.Station station : config.stations()) {
                if (!firstStation) writer.write(',');
                firstStation = false;
                writer.write(String.format(Locale.ROOT,
                        "{\"id\":\"%s\",\"band\":\"%s\",\"latitude_deg\":%.8f"
                                + ",\"longitude_deg\":%.8f,\"altitude_m\":%.3f"
                                + ",\"minimum_elevation_deg\":%.4f,\"passes\":[",
                        station.id(), station.band(), station.latitudeDeg(),
                        station.longitudeDeg(), station.altitudeM(), station.minimumElevationDeg()));
                List<PassPredictor.Pass> passes = new PassPredictor(orbit, station)
                        .predict(start, end, config.profileStepSeconds());
                boolean firstPass = true;
                for (PassPredictor.Pass pass : passes) {
                    if (!firstPass) writer.write(',');
                    firstPass = false;
                    writer.write(String.format(Locale.ROOT,
                            "{\"aos_utc\":\"%s\",\"los_utc\":\"%s\",\"peak_utc\":\"%s\""
                                    + ",\"max_elevation_deg\":%.4f,\"clipped_start\":%s,\"clipped_end\":%s}",
                            FdsOutput.utc(pass.aos()), FdsOutput.utc(pass.los()),
                            FdsOutput.utc(pass.peak()), pass.maxElevationDeg(),
                            pass.clippedStart(), pass.clippedEnd()));
                }
                writer.write("]}");
            }
            writer.write("],\"samples\":[");
            boolean firstSample = true;
            for (AbsoluteDate time = start; time.compareTo(end) < 0;
                 time = time.shiftedBy(config.ephemerisStepSeconds())) {
                if (!firstSample) writer.write(',');
                firstSample = false;
                sample(writer, orbit, earth, time);
            }
            if (!firstSample) writer.write(',');
            sample(writer, orbit, earth, end);
            writer.write("]}\n");
        }
    }

    private static void sample(BufferedWriter writer, BoundedPropagator orbit,
                               OneAxisEllipsoid earth, AbsoluteDate time) throws IOException {
        var itrf = earth.getBodyFrame();
        Vector3D position = orbit.getPVCoordinates(time, itrf).getPosition();
        GeodeticPoint subpoint = earth.transform(position, itrf, time);
        Vector3D sun = CelestialBodyFactory.getSun().getPosition(time, itrf).normalize();
        // Array order: UTC, ECEF xyz (m), longitude, latitude, altitude, Sun ECEF unit xyz.
        writer.write(String.format(Locale.ROOT,
                "[\"%s\",%.3f,%.3f,%.3f,%.7f,%.7f,%.3f,%.9f,%.9f,%.9f]",
                FdsOutput.utc(time), position.getX(), position.getY(), position.getZ(),
                Math.toDegrees(subpoint.getLongitude()), Math.toDegrees(subpoint.getLatitude()),
                subpoint.getAltitude(), sun.getX(), sun.getY(), sun.getZ()));
    }
}
