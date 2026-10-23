package kr.ac.khu.padosat.fds;

import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import org.orekit.propagation.BoundedPropagator;
import org.orekit.propagation.Propagator;

/** Offline file-to-file FDS calculation. No network listener or uplink route exists here. */
public final class FdsMain {
    private FdsMain() { }

    public static void main(String[] args) {
        try {
            Map<String, Path> options = parse(args);
            OrbitModel.configureOfflineData(options.get("--orekit-data"));
            FdsConfig config = FdsConfig.read(options.get("--config"));
            OrbitInput.Selected selected = OrbitInput.select(options.get("--tle"),
                    options.get("--gnss"), config.start(), config.noradId());
            Propagator model = OrbitModel.create(selected, config);
            BoundedPropagator orbit = OrbitModel.generate(model,
                    OrbitInput.date(config.start()).shiftedBy(config.horizonHours() * 3600.0));
            Path input = selected.gnss() != null ? options.get("--gnss") : options.get("--tle");
            FdsOutput.write(options.get("--out"), config, selected, input, orbit);
            System.out.println("FDS output: " + options.get("--out") + " source=" + selected.source());
        } catch (Exception error) {
            System.err.println("FDS failed: " + error.getMessage());
            System.exit(2);
        }
    }

    private static Map<String, Path> parse(String[] args) {
        if (args.length < 8 || args.length > 10 || args.length % 2 != 0) {
            throw new IllegalArgumentException("usage: --config FILE --orekit-data DIR --out NEW_DIR " +
                    "[--tle FILE] [--gnss FILE]");
        }
        Map<String, Path> values = new HashMap<>();
        for (int index = 0; index < args.length; index += 2) {
            String name = args[index];
            if (!name.matches("--(config|orekit-data|out|tle|gnss)") ||
                    values.putIfAbsent(name, Path.of(args[index + 1])) != null) {
                throw new IllegalArgumentException("unknown or repeated argument: " + name);
            }
        }
        if (!values.containsKey("--config") || !values.containsKey("--orekit-data") ||
                !values.containsKey("--out") ||
                (!values.containsKey("--tle") && !values.containsKey("--gnss"))) {
            throw new IllegalArgumentException("config, data, out and an orbit input are required");
        }
        return values;
    }
}
