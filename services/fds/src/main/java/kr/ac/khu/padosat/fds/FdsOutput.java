package kr.ac.khu.padosat.fds;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;

import org.orekit.frames.FramesFactory;
import org.orekit.propagation.BoundedPropagator;
import org.orekit.time.AbsoluteDate;
import org.orekit.time.TimeScalesFactory;
import org.orekit.utils.PVCoordinates;

/** Writes a complete run to a new directory before making any result visible. */
public final class FdsOutput {
    private static final double LIGHT_SPEED_MPS = 299792458.0;

    private FdsOutput() { }

    public static void write(Path destination, FdsConfig config, OrbitInput.Selected source,
                             Path inputPath, BoundedPropagator orbit) throws IOException {
        Path absolute = destination.toAbsolutePath().normalize();
        Path parent = absolute.getParent();
        if (parent == null || !Files.isDirectory(parent) || Files.exists(absolute)) {
            throw new IllegalArgumentException("output must be a new directory under an existing parent");
        }
        Path staging = Files.createTempDirectory(parent, "fds-staging-");
        try {
            AbsoluteDate start = OrbitInput.date(config.start());
            AbsoluteDate end = start.shiftedBy(config.horizonHours() * 3600.0);
            writeSchedule(staging.resolve("contact_schedule.csv"), config, orbit, start, end);
            writeProfile(staging.resolve("pass_information.csv"), config, orbit, start, end);
            writeEphemeris(staging.resolve("orbit_ephemeris.csv"), config, orbit, start, end);
            writeMetadata(staging.resolve("run.properties"), config, source, inputPath);
            manifest(staging, "contact_schedule.csv", "CONTACT_SCHEDULE", "mps");
            manifest(staging, "pass_information.csv", "PASS_INFORMATION", "dgs-archive");
            manifest(staging, "orbit_ephemeris.csv", "ORBIT_EPHEMERIS", "dgs-archive");
            Files.move(staging, absolute, StandardCopyOption.ATOMIC_MOVE);
        } catch (AtomicMoveNotSupportedException error) {
            throw new IOException("atomic output publication is unavailable", error);
        } finally {
            if (Files.exists(staging)) {
                try (var files = Files.list(staging)) {
                    for (Path file : files.toList()) {
                        Files.deleteIfExists(file);
                    }
                }
                Files.deleteIfExists(staging);
            }
        }
    }

    private static void writeSchedule(Path file, FdsConfig config, BoundedPropagator orbit,
                                      AbsoluteDate start, AbsoluteDate end) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
            writer.write("schema,station_id,band,pass_id,aos_utc,los_utc,peak_utc,max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end\n");
            for (FdsConfig.Station station : config.stations()) {
                PassPredictor predictor = new PassPredictor(orbit, station);
                for (PassPredictor.Pass pass : predictor.predict(start, end, config.profileStepSeconds())) {
                    writer.write(String.format(Locale.ROOT,
                            "fds.contact-schedule.v1,%s,%s,%s,%s,%s,%s,%.6f,%.6f,%s,%s%n",
                            station.id(), station.band(), pass.id(), utc(pass.aos()), utc(pass.los()),
                            utc(pass.peak()), pass.maxElevationDeg(), station.minimumElevationDeg(),
                            pass.clippedStart(), pass.clippedEnd()));
                }
            }
        }
    }

    private static void writeProfile(Path file, FdsConfig config, BoundedPropagator orbit,
                                     AbsoluteDate start, AbsoluteDate end) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
            writer.write("schema,station_id,band,pass_id,utc,azimuth_deg,elevation_deg,range_m,range_rate_mps,carrier_hz,doppler_shift_hz,received_hz,frequency_example\n");
            for (FdsConfig.Station station : config.stations()) {
                PassPredictor predictor = new PassPredictor(orbit, station);
                List<PassPredictor.Pass> passes = predictor.predict(start, end, config.profileStepSeconds());
                for (PassPredictor.Pass pass : passes) {
                    for (AbsoluteDate time = pass.aos(); time.compareTo(pass.los()) < 0;
                         time = time.shiftedBy(config.profileStepSeconds())) {
                        profileRow(writer, predictor, pass, time);
                    }
                    profileRow(writer, predictor, pass, pass.los());
                }
            }
        }
    }

    private static void profileRow(BufferedWriter writer, PassPredictor predictor,
                                   PassPredictor.Pass pass, AbsoluteDate time) throws IOException {
        PassPredictor.Observation observation = predictor.at(time);
        FdsConfig.Station station = pass.station();
        double shift = -station.carrierHz() * observation.rangeRateMps() / LIGHT_SPEED_MPS;
        writer.write(String.format(Locale.ROOT,
                "fds.pass-information.v1,%s,%s,%s,%s,%.6f,%.6f,%.3f,%.6f,%.3f,%.6f,%.6f,%s%n",
                station.id(), station.band(), pass.id(), utc(time), observation.azimuthDeg(),
                observation.elevationDeg(), observation.rangeM(), observation.rangeRateMps(),
                station.carrierHz(), shift, station.carrierHz() + shift,
                station.exampleFrequency()));
    }

    private static void writeEphemeris(Path file, FdsConfig config, BoundedPropagator orbit,
                                       AbsoluteDate start, AbsoluteDate end) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
            writer.write("schema,utc,frame,x_m,y_m,z_m,vx_mps,vy_mps,vz_mps\n");
            for (AbsoluteDate time = start; time.compareTo(end) < 0;
                 time = time.shiftedBy(config.ephemerisStepSeconds())) {
                ephemerisRow(writer, orbit, time);
            }
            ephemerisRow(writer, orbit, end);
        }
    }

    private static void ephemerisRow(BufferedWriter writer, BoundedPropagator orbit,
                                     AbsoluteDate time) throws IOException {
        PVCoordinates pv = orbit.getPVCoordinates(time, FramesFactory.getGCRF());
        writer.write(String.format(Locale.ROOT,
                "fds.orbit-ephemeris.v1,%s,GCRF,%.3f,%.3f,%.3f,%.6f,%.6f,%.6f%n",
                utc(time), pv.getPosition().getX(), pv.getPosition().getY(),
                pv.getPosition().getZ(), pv.getVelocity().getX(), pv.getVelocity().getY(),
                pv.getVelocity().getZ()));
    }

    private static void writeMetadata(Path file, FdsConfig config, OrbitInput.Selected source,
                                      Path input) throws IOException {
        String text = "schema=fds.run.v1\n" + "expected_norad_id=" + config.noradId() + "\n" +
                "source=" + source.source() + "\n" +
                "selection_reason=" + source.reason() + "\n" + "model=" + source.model() + "\n" +
                "last_gnss_epoch_utc=" + (source.lastGnssEpoch() == null ? "NONE" :
                        source.lastGnssEpoch()) + "\n" +
                "quality=PRELIMINARY_NOT_FOR_OPERATIONS\n" + "input_epoch_utc=" + utc(source.epoch()) + "\n" +
                "input_sha256=" + sha256(input) + "\n" + "start_utc=" + config.start() + "\n" +
                "horizon_hours=" + config.horizonHours() + "\n" +
                "ephemeris_step_s=" + config.ephemerisStepSeconds() + "\n" +
                "profile_step_s=" + config.profileStepSeconds() + "\n" +
                "gravity_degree=" + config.gravityDegree() + "\n" +
                "gravity_order=" + config.gravityOrder() + "\n" +
                "spacecraft_mass_kg=" + config.spacecraftMassKg() + "\n" +
                "force_models=" + (source.gnss() == null ? "SGP4" :
                        "EARTH_HARMONICS,SUN_GRAVITY,MOON_GRAVITY") + "\n" +
                "omitted_force_models=" + (source.gnss() == null ? "NOT_APPLICABLE" :
                        "ATMOSPHERIC_DRAG,SOLAR_RADIATION_PRESSURE") + "\n" +
                "orekit_version=13.1.9\n";
        StringBuilder full = new StringBuilder(text);
        for (FdsConfig.Station station : config.stations()) {
            String prefix = "station." + station.id() + ".";
            full.append(prefix).append("band=").append(station.band()).append('\n');
            full.append(prefix).append("latitude_deg=").append(station.latitudeDeg()).append('\n');
            full.append(prefix).append("longitude_deg=").append(station.longitudeDeg()).append('\n');
            full.append(prefix).append("altitude_m=").append(station.altitudeM()).append('\n');
            full.append(prefix).append("minimum_elevation_deg=")
                    .append(station.minimumElevationDeg()).append('\n');
            full.append(prefix).append("carrier_hz=").append(station.carrierHz()).append('\n');
            full.append(prefix).append("frequency_example=")
                    .append(station.exampleFrequency()).append('\n');
        }
        Files.writeString(file, full.toString(), StandardCharsets.UTF_8);
    }

    private static void manifest(Path directory, String name, String type, String destination)
            throws IOException {
        String created = Instant.now().truncatedTo(ChronoUnit.SECONDS).toString();
        String json = String.format(Locale.ROOT,
                "{\"file\":\"%s\",\"type\":\"%s\",\"from\":\"fds\",\"to\":\"%s\",\"version\":1,\"created\":\"%s\",\"sha256\":\"%s\"}%n",
                name, type, destination, created, sha256(directory.resolve(name)));
        Files.writeString(directory.resolve(name + ".manifest.json"), json, StandardCharsets.UTF_8);
    }

    static String sha256(Path path) throws IOException {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream stream = new DigestInputStream(Files.newInputStream(path), digest)) {
                stream.transferTo(java.io.OutputStream.nullOutputStream());
            }
            return HexFormat.of().formatHex(digest.digest());
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }

    static String utc(AbsoluteDate date) {
        return date.toStringWithoutUtcOffset(TimeScalesFactory.getUTC(), 3) + "Z";
    }
}
