package kr.ac.khu.padosat.fds;

import java.nio.file.Files;
import java.nio.file.Path;

import org.hipparchus.ode.nonstiff.DormandPrince853Integrator;
import org.orekit.bodies.CelestialBodyFactory;
import org.orekit.data.DataContext;
import org.orekit.data.DirectoryCrawler;
import org.orekit.forces.gravity.HolmesFeatherstoneAttractionModel;
import org.orekit.forces.gravity.ThirdBodyAttraction;
import org.orekit.forces.gravity.potential.GravityFieldFactory;
import org.orekit.frames.FramesFactory;
import org.orekit.orbits.CartesianOrbit;
import org.orekit.orbits.Orbit;
import org.orekit.orbits.OrbitType;
import org.orekit.propagation.BoundedPropagator;
import org.orekit.propagation.EphemerisGenerator;
import org.orekit.propagation.Propagator;
import org.orekit.propagation.SpacecraftState;
import org.orekit.propagation.ToleranceProvider;
import org.orekit.propagation.analytical.tle.TLEPropagator;
import org.orekit.propagation.numerical.NumericalPropagator;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.Constants;
import org.orekit.utils.IERSConventions;

/** Orekit propagation with explicit model selection and offline physical data. */
public final class OrbitModel {
    private OrbitModel() { }

    public static void configureOfflineData(Path data) {
        if (!Files.isDirectory(data)) {
            throw new IllegalArgumentException("Orekit physical data directory is required");
        }
        DataContext.getDefault().getDataProvidersManager().addProvider(new DirectoryCrawler(data.toFile()));
    }

    public static Propagator create(OrbitInput.Selected selected, FdsConfig config) {
        if (selected.tle() != null) {
            TLEPropagator propagator = TLEPropagator.selectExtrapolator(selected.tle());
            if (!"SGP4".equals(propagator.getClass().getSimpleName())) {
                throw new IllegalArgumentException("TLE requires SDP4; this FDS path is SGP4 only");
            }
            return propagator;
        }
        OrbitInput.GnssState input = selected.gnss();
        var itrf = FramesFactory.getITRF(IERSConventions.IERS_2010, true);
        var pvGcrf = itrf.getTransformTo(FramesFactory.getGCRF(), selected.epoch())
                .transformPVCoordinates(input.pv());
        Orbit orbit = new CartesianOrbit(pvGcrf, FramesFactory.getGCRF(), selected.epoch(),
                Constants.EGM96_EARTH_MU);
        double[][] tolerance = ToleranceProvider.getDefaultToleranceProvider(1.0)
                .getTolerances(orbit, OrbitType.CARTESIAN);
        DormandPrince853Integrator integrator = new DormandPrince853Integrator(
                0.01, 300.0, tolerance[0], tolerance[1]);
        NumericalPropagator propagator = new NumericalPropagator(integrator);
        propagator.setOrbitType(OrbitType.CARTESIAN);
        propagator.setInitialState(new SpacecraftState(orbit).withMass(config.spacecraftMassKg()));
        propagator.addForceModel(new HolmesFeatherstoneAttractionModel(
                FramesFactory.getITRF(IERSConventions.IERS_2010, true),
                GravityFieldFactory.getNormalizedProvider(config.gravityDegree(), config.gravityOrder())));
        propagator.addForceModel(new ThirdBodyAttraction(CelestialBodyFactory.getSun()));
        propagator.addForceModel(new ThirdBodyAttraction(CelestialBodyFactory.getMoon()));
        return propagator;
    }

    public static BoundedPropagator generate(Propagator propagator, AbsoluteDate end) {
        EphemerisGenerator generator = propagator.getEphemerisGenerator();
        propagator.propagate(end);
        return generator.getGeneratedEphemeris();
    }
}
