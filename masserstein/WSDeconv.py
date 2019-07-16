#! /usr/bin/python3
from masserstein import Spectrum
from masserstein import estimate_proportions
from getopt import getopt
import numpy as np
import sys
import re

doc = """NAME:
    WSDeconv

USAGE:
    python3 WSDeconv [OPTIONS] MASS_SPECTRUM MOLECULE_LIST [OUTPUT]

EXAMPLES:
    python3 WSDeconv examples/small_molecule_spectrum.txt "C2H5OH,C3H8,C2H4"
    python3 WSDeconv examples/small_molecule_spectrum.txt examples/small_molecule_list.txt Small_example
    python3 WSDeconv examples/protein_spectrum.txt examples/protein_molecule_list.txt Proteins_example

DESCRIPTION:
    Deconvolves overlapping isotopic distributions and returns their proportions.
    MASS_SPECTRUM file contains the subject spectrum in a peak list format.
    MOLECULE_LIST is either an elemental formula, or a file with a list of
    elemental formulas of ions which proportions in the spectrum are to be estimated.
    Lines starting with a hash # in any file are treated as comments and skipped.
    OUTPUT is the filename prefix of output files. The program writes out the following files:
    OUTPUT_proportions.txt, which stores the list of estimated proportions;
    OUTPUT_denoised.txt, which stores the denoised experimental spectrum;
    OUTPUT_fitted.txt, which stores a fitted theoretical spectrum (a linear combination
    of theoretical spectra of input molecules with optimal proportions);
    OUTPUT_transport.txt, which stores an optimal transport plan from
    the experimental spectrum to the fitted theoretical spectrum;
    OUTPUT_log.txt, which stores the information about the program run.
    If this argument is ommited, the program run information and
    molecule proportions are written to standard output.

    Each elemental formula should consist of three parts:
    a formula of the neutral state molecule, followed by charge sign (plus/minus),
    followed by adduct formula. The charge is determined by the number of adducts atoms.
    If the charge is negative, the adducts are subtracted from the formula.
    An example formula is NH3 + H for ammonium.
    If only the neutral part is supplied, it is assumed that it represents a complete formula
    of a singly-charged ion. Therefore, writing NH3 + H is equivalent to NH4,
    and COOH - H is equivalent to COO. However, simply writing 'NH3 ' will inform the program
    that this is a singly-charged modecule!

OPTIONS:
    -h
        Print this message and exit.
    -p: float in [0, 1], default: 0.95
        Theoretical isotopic envelope coverage.
        This is the percentage of total peak intensity included in the theoretical spectra
        generated by IsoSpec.
        Larger values lead to more accurate theoretical isotopic envelopes.
        This may improve results accuracy, but increase the computational complexity.
        If the experimental spectrum is processed (e.g. peak-picked and/or deisotoped), this
        value needs to be adjusted accordingly to reflect the observed signal.
    -t: float, default: 0.01
        The denoising penalty, interpretable as a maximum feasible distance of ion transport
        or as the cost of assuming that there is no signal in the subject spectrum. 
        The proportion of signal explained by a given theoretical envelope
        is mostly computed from ion current around theoretical peaks within this margin.
        Experimental ion current that cannot be explained by ion current transport
        is treated as background or chemical noise, so that the method performs simultaneous
        proportion estimation and denoising.
        Setting this value to -1 disables denoising.
    -c: float, default: 1e-12
        Minimum detectable ion current. If a theoretical isotopic envelope matches less than
        this proportion of experimental ion current, it is filtered out during preprocessing
        and it's proportion is assumed to be zero.
        Defaults to 0, which effectively disables filtering.
    -d: float, default: 2.1
        Mode Matching Distance. If the highest peak of a theoretical isotopic envelope
        does not match any experimental peak within this distance, this envelope is
        filtered out during preprocessing and it's proportion is assumed to be zero.
        Set to -1 to disable filtering.
    -s
        Suppress writing additional output files - write out only proportions.
    -v
        Print detailed diagnostic messages.

CONTACT:
    If you encounter any problems during use of this application, please email me at m_ciach@student.uw.edu.pl.
"""

to_add_when_implemented = """
    Elemental formulas may be also in the form of 'molecular regular expressions',
    e.g. CH3(CH2)[2,5-7]NH2 + H[0-1]Na[0-1]. This will expand the CH2 part either 2 or from 5 to 7
    times and, for each chain length, will add a hydrogen, a sodium, or both atoms.
    As before, the first part is assumed represent a neutral state of the molecule.
    """

mass_warning = """
WARNING: Detected a large-distance mass transport between masses %.2f and %.2f.
Please report this to the authors.
"""

def main():
    penalty = 0.1  # denoising penalty
    prob = 0.999  # minimum theoretical envelope coverage
    MMD = 2.1  # maximum mode distance
    MDC = 1e-12 # minimum detectable current
    only_proportions = False
    verbose = False

    opts, args = getopt(sys.argv[1:], 'hp:t:c:d:sv')

    if not args:
        print(doc)
        quit()

    for opt, arg in opts:
        if opt == '-h':
            print(doc)
            quit()
        if opt == '-p':
            prob = float(arg)
            assert 0 <= prob <= 1, 'Improper isotopic envelope coverage value: %f' % prob
        if opt == '-t':
            penalty = float(arg)
            assert penalty == -1 or penalty >= 0, 'Improper maximum transport distance: %f' % thr
        if opt == '-c':
            MDC = float(arg)
            assert MDC >= 0, 'Improper Minimum Detectable Current value: %f' % MDC
        if opt == '-d':
            MMD = float(arg)
            assert MMD == -1 or MMD >= 0, 'Improper Maximum Mode Distance: %f' % MMD
        if opt == '-v':
            verbose = True
        if opt == '-s':
            only_proportions = True

    spectrum, molecules = args[:2]
    try:
        output = args[2]
    except IndexError:
        output = None

    LOG = 'WsDeconv initialized.\n'
    LOG += "Experimental spectrum: " + spectrum + '\n'
    LOG += "Molecule list: " + molecules + '\n'
    LOG += "Theoretical envelope coverage: " + str(prob) + '\n'
    if penalty == -1:
        LOG += "Maximum transport distance (denoising penalty): infinite" + '\n'
    else:
        LOG += "Maximum transport distance (denoising penalty): " + str(penalty) + '\n'
    if MMD == -1:
        LOG += 'Mode matching filtering disabled\n'
    else:
        LOG += 'Minimum mode matching distance: ' + str(MMD) + '\n'
    if MDC == 0:
        LOG += 'Minimum current coverage filtering disabled\n'
    else:
        LOG += 'Minimum current coverage: ' + str(MDC) + '\n'

    print(LOG)

    # Parse molecule list & construct list of theoretical spectra:
    def parse_mol_formula(mstr):
        """
        Expands the molecular regular expression mstr to a list of formulas.
        Each element of the returned list is a tuple with a full formula, the adduct formula, and charge.
        """
        formulas = []
        charge_sign = re.findall('[+-]', mstr)
        if len(charge_sign) == 1:
            charge_sign = charge_sign[0]
            neutral, adduct = mstr.split(charge_sign)
            neutral = neutral.strip()
            adduct = adduct.strip()
            adduct_parsed = re.findall('([A-Z][a-z]*)([0-9]*)', adduct)
            adduct, charge = adduct_parsed[0]
            charge = int(charge)
    ##        for e, n in adduct_parsed:
    ##            charge += int(n) if n else 1
        elif len(charge_sign) == 0:
            neutral, adduct = mstr.strip(), None
            charge = 1
        else:
            raise ValueError('Improper charge signs:' + str(charge_sign))
        return (neutral, adduct, charge)

    try:
        molecules = open(molecules).readlines()
    except IOError:
        molecules = molecules.split(',')
    if verbose:
        print('Read molecules:')
        print(molecules)
    molecules = [m.strip() for m in molecules if m and m[0] != '#']
    parsed_molecules = [parse_mol_formula(m) for m in molecules]
    if verbose:
        print('Molecule\tAdduct\tCharge')
        for n,a,c in parsed_molecules:
            print(n,a,c,sep='\t')
    thr_spctrs = [Spectrum(f, threshold = 1-prob, intensity=1.0 , charge=c, adduct=a) for f, a, c in parsed_molecules]
    for s in thr_spctrs:
        s.normalize()

    # Parsing spectrum:
    peaklist = open(spectrum).readlines()
    peaklist = [list(map(float, l.strip().split())) for l in peaklist if l and l[0] != '#']

    # Spectrum initialization:
    spectrum = Spectrum("", empty=True)
    spectrum.set_confs(peaklist)
    del peaklist

    # Normalize and obtain mz range:
    spectrum.normalize()
    mass_range = spectrum.confs[-1][0] - spectrum.confs[0][0]
    if penalty == -1:
        penalty = mass_range + 10.

    # Proportion estimation:
    result = estimate_proportions(spectrum, thr_spctrs, penalty, MDC, MMD, verbose)
    total_signal = sum(result['proportions'])
    result['proportions'] = [p/total_signal for p in result['proportions']]

    # Parsing results:
    if result:
        print('Amount of noise detected: %f' % sum(result['noise']))
        LOG += 'Amount of noise detected: %f' % sum(result['noise']) + '\n'
        # Obtain fitted theoretical spectrum:
        fitted = Spectrum("", empty=True)
        # normalized_proportions = [w/sum(result['proportions']) for w in result['proportions']]
        for s, w in zip(thr_spctrs, result['proportions']):
            fitted += s*w
        fitted.normalize()

        # Obtain denoised spectrum:
        denoised = Spectrum('', empty=True)
        denoised.set_confs([(p[0], p[1]-n ) for p, n in zip(spectrum.confs, result['noise']) if not np.isclose(p[1]-n, 0.)])
        denoised.normalize()

        # Obtain transport plan:
        mvs = [mv for mv in fitted.WSDistanceMoves(denoised) if mv[2] > 1e-06]
        max_mv = max(mvs, key=lambda x: abs(x[1]-x[0]))
    ##    if abs(max_mv[1]-max_mv[0]) > penalty:
    ##        print((mass_warning % (max_mv[0], max_mv[1])))
        wsdist = denoised.WSDistance(fitted)

        print()
        print('Optimal Wasserstein distance with denoising penalty: %f' % (wsdist + penalty*sum(result['noise'])))
        print("Optimal Wasserstein distance: %f" % wsdist)
        print()
        LOG += 'Optimal Wasserstein distance with denoising penalty: %f' % (wsdist + penalty*sum(result['noise'])) + '\n'
        LOG += "Optimal Wasserstein distance: %f" % wsdist + '\n'

        if not output:
            print("Isotopic envelope proportions:")
            for m, d in zip(molecules, result['proportions']):
                print(m + '\t' + str(round(d, 10)))
            print()
        else:
            with open(output+'_proportions.txt', 'w') as h:
                for m, d in zip(molecules, result['proportions']):
                    h.write(m + '\t' + str(d) + '\n')
                print('Optimal molecule proportions written to', output+'_proportions.txt')
            with open(output+'_denoised.txt', 'w') as h:
                for m, i in denoised.confs:
                    h.write(str(m) + '\t'  + str(i) + '\n')
                print('Denoised experimental spectrum written to', output+'_denoised.txt')
            with open(output+'_fitted.txt', 'w') as h:
                for m, i in fitted.confs:
                    h.write(str(m) + '\t' + str(i) + '\n')
                print('Fitted theoretical spectrum written to', output + '_fitted.txt')
            with open(output+'_transport.txt', 'w') as h:
                for em, tm, i in mvs:
                    h.write(str(em) + '\t' + str(tm) + '\t' + str(i) + '\n')
                print('Optimal transport plan written to', output+'_transport.txt')
            with open(output+'_log.txt', 'w') as h:
                h.write(LOG)
                print('Program run information written to', output+'_log.txt')
    else:
        print("Deconvolution failed. Please report this to the authors.")


if __name__ == "__main__":
    main()
