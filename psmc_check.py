#!/usr/bin/env python

"""
========================
psmc_check
========================

This code generates backstop load review outputs for checking the ACIS
PSMC temperature 1PDEAAT.  It also generates 1PDEAAT model validation
plots comparing predicted values to telemetry for the previous three weeks.
"""

import sys
import os
import logging

import numpy as np
from numpy import ndarray

import Chandra.cmd_states as cmd_states
# Matplotlib setup
# Use Agg backend for command-line (non-interactive) operation
import matplotlib
if __name__ == '__main__':
    matplotlib.use('Agg')

import xija

from model_check import ModelCheck, calc_off_nom_rolls

MSID = dict(psmc='1PDEAAT', pin='1PIN1AT')
YELLOW = dict(psmc=55.0,pin=38.0)
MARGIN = dict(psmc=2.5, pin=2.5)
VALIDATION_LIMITS = {'1PDEAAT': [(1, 2.5),
                                 (50, 1.0),
                                 (99, 5.5)],
                     '1PIN1AT' :  ((1, 5.5),
                                    (99, 5.5)),
                     'PITCH': [(1, 3.0),
                                  (99, 3.0)],
                     'TSCPOS': [(1, 2.5),
                                (99, 2.5)]
                     }

TASK_DATA = os.path.dirname(__file__)
URL = "http://cxc.harvard.edu/mta/ASPECT/psmc_daily_check"

logger = logging.getLogger('psmc_check')

_versionfile = os.path.join(os.path.dirname(__file__), 'VERSION')
VERSION = open(_versionfile).read().strip()

def get_options():
    from optparse import OptionParser
    parser = OptionParser()
    parser.set_defaults()
    parser.add_option("--outdir",
                      default="out",
                      help="Output directory")
    parser.add_option("--oflsdir",
                       help="Load products OFLS directory")
    parser.add_option("--model-spec",
                      default=os.path.join(TASK_DATA, 'psmc_model_spec.json'),
                       help="PSMC model specification file")
    parser.add_option("--days",
                      type='float',
                      default=21.0,
                      help="Days of validation data (days)")
    parser.add_option("--run-start",
                      help="Reference time to replace run start time "
                           "for regression testing")
    parser.add_option("--traceback",
                      default=True,
                      help='Enable tracebacks')
    parser.add_option("--verbose",
                      type='int',
                      default=1,
                      help="Verbosity (0=quiet, 1=normal, 2=debug)")
    parser.add_option("--ccd-count",
                      type='int',
                      default=6,
                      help="Initial number of CCDs (default=6)")
    parser.add_option("--fep-count",
                      type='int',
                      default=6,
                      help="Initial number of FEPs (default=6)")
    parser.add_option("--vid-board",
                      type='int',
                      default=1,
                      help="Initial state of ACIS vid_board (default=1)")
    parser.add_option("--clocking",
                      type='int',
                      default=1,
                      help="Initial state of ACIS clocking (default=1)")
    parser.add_option("--simpos",
                      default=75616,
                      type='float',
                      help="Starting SIM-Z position (steps)")
    parser.add_option("--pitch",
                      default=150.0,
                      type='float',
                      help="Starting pitch (deg)")
    parser.add_option("--T-psmc",
                      type='float',
                      help="Starting 1PDEAAT temperature (degC)")
    parser.add_option("--T-pin1at",
                      type='float',
                      help="Starting 1PIN1AT temperature (degC)")
    # adding dh_heater
    parser.add_option("--dh_heater",
                      type='int',
                      default=0,
                      help="Starting Detector Housing Heater state")
    parser.add_option("--version",
                      action='store_true',
                      help="Print version")

    opt, args = parser.parse_args()
    return opt, args


def main(opt):
    if not os.path.exists(opt.outdir):
        os.mkdir(opt.outdir)

    config_logging(opt.outdir, opt.verbose)

    # Store info relevant to processing for use in outputs
    proc = dict(run_user=os.environ['USER'],
                run_time=time.ctime(),
                errors=[],
                psmc_limit=YELLOW['psmc'] - MARGIN['psmc'],
                )
    logger.info('##############################'
                '#######################################')
    logger.info('# psmc_check.py run at %s by %s'
                % (proc['run_time'], proc['run_user']))
    logger.info('# psmc_check version = {}'.format(VERSION))
    logger.info('# model_spec file = %s' % os.path.abspath(opt.model_spec))
    logger.info('###############################'
                '######################################\n')

    logger.info('Command line options:\n%s\n' % pformat(opt.__dict__))

    # Connect to database (NEED TO USE aca_read)
    logger.info('Connecting to database to get cmd_states')
    db = Ska.DBI.DBI(dbi='sybase', server='sybase', user='aca_read',
                     database='aca')

    tnow = DateTime(opt.run_start).secs
    if opt.oflsdir is not None:
        # Get tstart, tstop, commands from backstop file in opt.oflsdir
        bs_cmds = get_bs_cmds(opt.oflsdir)
        tstart = bs_cmds[0]['time']
        tstop = bs_cmds[-1]['time']

        proc.update(dict(datestart=DateTime(tstart).date,
                         datestop=DateTime(tstop).date))
    else:
        tstart = tnow

    # Get temperature telemetry for 3 weeks prior to min(tstart, NOW)
    tlm = get_telem_values(min(tstart, tnow),
                           ['1pdeaat','1pin1at',
                            'sim_z', 'aosares1',
                            'dp_dpa_power','1dahtbon'],
                           days=opt.days,
                           name_map={'sim_z': 'tscpos',
                                     'aosares1': 'pitch',
                                     '1dahtbon': 'dh_heater'})
    tlm['tscpos'] = tlm['tscpos'] * -397.7225924607

    # make predictions on oflsdir if defined
    if opt.oflsdir is not None:
        pred = make_week_predict(opt, tstart, tstop, bs_cmds, tlm, db)
    else:
        pred = dict(plots=None, viols=None, times=None, states=None,
                    temps=None)

    # Validation
    plots_validation = make_validation_plots(opt, tlm, db)
    valid_viols = make_validation_viols(plots_validation)
    if len(valid_viols) > 0:
        # generate daily plot url if outdir in expected year/day format
        daymatch = re.match('.*(\d{4})/(\d{3})', opt.outdir)
        if opt.oflsdir is None and daymatch:
            url = os.path.join(URL, daymatch.group(1), daymatch.group(2))
            logger.info('validation warning(s) at %s' % url)
        else:
            logger.info('validation warning(s) in output at %s' % opt.outdir)

    write_index_rst(opt, proc, plots_validation, valid_viols=valid_viols,
                    plots=pred['plots'], viols=pred['viols'])
    rst_to_html(opt, proc)

    return dict(opt=opt, states=pred['states'], times=pred['times'],
                temps=pred['temps'], plots=pred['plots'],
                viols=pred['viols'], proc=proc,
                plots_validation=plots_validation)

def calc_model(model_spec, states, start, stop, T_psmc=None, T_psmc_times=None, 
               T_pin1at=None,T_pin1at_times=None,
               dh_heater=None,dh_heater_times=None):
    model = xija.XijaModel('psmc', start=start, stop=stop,
                              model_spec=model_spec)
    

    #set fetch to quiet if and only if verbose == 0
    if opt.verbose == 0:
        xija.logger.setLevel(100);

    times = np.array([states['tstart'], states['tstop']])
    model.comp['sim_z'].set_data(states['simpos'], times)
    #model.comp['eclipse'].set_data(False)
    model.comp['1pdeaat'].set_data(T_psmc, T_psmc_times)
    model.comp['pin1at'].set_data(T_pin1at,T_pin1at_times)
    model.comp['roll'].set_data(calc_off_nom_rolls(states), times)
    model.comp['eclipse'].set_data(False)

    # for name in ('ccd_count', 'fep_count', 'vid_board', 'clocking', 'pitch', 'dh_heater'):
    for name in ('ccd_count', 'fep_count', 'vid_board', 'clocking', 'pitch'):
        model.comp[name].set_data(states[name], times)

    model.comp['dh_heater'].set_data(dh_heater,dh_heater_times)

    model.make()
    model.calc()
    return model


def make_week_predict(opt, tstart, tstop, bs_cmds, tlm, db):
    logger.debug("In make_week_predict")

    # Try to make initial state0 from cmd line options
    state0 = dict((x, getattr(opt, x))
                  for x in ('pitch', 'simpos', 'ccd_count', 'fep_count',
                            'vid_board', 'clocking', 'T_psmc','T_pin1at',
                            'dh_heater'))
    
    state0.update({'tstart': tstart - 30,
                   'tstop': tstart,
                   'datestart': DateTime(tstart - 30).date,
                   'datestop': DateTime(tstart).date,
                   'q1': 0.0, 'q2': 0.0, 'q3': 0.0, 'q4': 1.0,
                   }
                  )

    logger.debug("Completed state0 update")
    # If cmd lines options were not fully specified then get state0 as last
    # cmd_state that starts within available telemetry.  Update with the
    # mean temperatures at the start of state0.
    if None in state0.values():
        state0 = cmd_states.get_state0(tlm['date'][-5], db,
                                       datepar='datestart')
        ok = ((tlm['date'] >= state0['tstart'] - 700) &
              (tlm['date'] <= state0['tstart'] + 700))
        state0.update({'T_psmc': np.mean(tlm['1pdeaat'][ok])})
        # state0.update({'T_pin1at': np.mean(tlm['1pin1at'][ok]) + 3.0 })
        state0.update({'T_pin1at': np.mean(tlm['1pdeaat'][ok]) - 10.0 })

        

    # TEMPORARY HACK: core model doesn't actually support predictive
    # active heater yet.  Initial temperature determines active heater
    # state for predictions now.
    if state0['T_psmc'] < 15:
        state0['T_psmc'] = 15.0

    logger.info('state0 at %s is\n%s' % (DateTime(state0['tstart']).date,
                                           pformat(state0)))

    # Get commands after end of state0 through first backstop command time
    cmds_datestart = state0['datestop']
    cmds_datestop = bs_cmds[0]['date']

    # Get timeline load segments including state0 and beyond.
    timeline_loads = db.fetchall("""SELECT * from timeline_loads
                                 WHERE datestop > '%s'
                                 and datestart < '%s'"""
                                 % (cmds_datestart, cmds_datestop))
    logger.info('Found {} timeline_loads  after {}'.format(
            len(timeline_loads), cmds_datestart))

    # Get cmds since datestart within timeline_loads
    db_cmds = cmd_states.get_cmds(cmds_datestart, db=db, update_db=False,
                                  timeline_loads=timeline_loads)

    # Delete non-load cmds that are within the backstop time span
    # => Keep if timeline_id is not None or date < bs_cmds[0]['time']
    db_cmds = [x for x in db_cmds if (x['timeline_id'] is not None or
                                      x['time'] < bs_cmds[0]['time'])]

    logger.info('Got %d cmds from database between %s and %s' %
                  (len(db_cmds), cmds_datestart, cmds_datestop))

    # Get the commanded states from state0 through the end of backstop commands
    states = cmd_states.get_states(state0, db_cmds + bs_cmds)
    states[-1].datestop = bs_cmds[-1]['date']
    states[-1].tstop = bs_cmds[-1]['time']
    logger.info('Found %d commanded states from %s to %s' %
                 (len(states), states[0]['datestart'], states[-1]['datestop']))

    # htrbfn='/home/edgar/acis/thermal_models/dhheater_history/dahtbon_history.rdb'
    htrbfn='dahtbon_history.rdb'
    logger.info('Reading file of dahtrb commands from file %s' % htrbfn)
    htrb=Ska.Table.read_ascii_table(htrbfn,headerrow=2,headertype='rdb')
    dh_heater_times=Chandra.Time.date2secs(htrb['time'])
    dh_heater=htrb['dahtbon'].astype(bool)

    # Create array of times at which to calculate PSMC temps, then do it.
    logger.info('Calculating PSMC thermal model')
    logger.info('state0 at start of calc is\n%s' % (pformat(state0)))

    model = calc_model(opt.model_spec, states, state0['tstart'], tstop,
                       state0['T_psmc'],None,state0['T_pin1at'], None,
                       dh_heater,dh_heater_times)

    # Make the PSMC limit check plots and data files
    plt.rc("axes", labelsize=10, titlesize=12)
    plt.rc("xtick", labelsize=10)
    plt.rc("ytick", labelsize=10)
    temps = dict(psmc=model.comp['1pdeaat'].mvals,pin=model.comp['pin1at'].mvals)
    plots = make_check_plots(opt, states, model.times, temps, tstart)
    viols = make_viols(opt, states, model.times, temps)
    write_states(opt, states)
    write_temps(opt, model.times, temps)

    return dict(opt=opt, states=states, times=model.times, temps=temps,
               plots=plots, viols=viols)

def make_validation_plots(opt, tlm, db):
    """
    Make validation output plots.

    :param outdir: output directory
    :param tlm: telemetry
    :param db: database handle
    :returns: list of plot info including plot file names
    """
    outdir = opt.outdir
    start = tlm['date'][0]
    stop = tlm['date'][-1]
    states = get_states(start, stop, db)

    # Create array of times at which to calculate PSMC temperatures, then do it
    logger.info('Calculating PSMC thermal model for validation')

    model = calc_model(opt.model_spec, states, start, stop)

    # Interpolate states onto the tlm.date grid
    # state_vals = cmd_states.interpolate_states(states, model.times)
    pred = {'1pdeaat': model.comp['1pdeaat'].mvals,
            'pitch': model.comp['pitch'].mvals,
            'tscpos': model.comp['sim_z'].mvals
            }

    idxs = Ska.Numpy.interpolate(np.arange(len(tlm)), tlm['date'], model.times,
                                 method='nearest')
    tlm = tlm[idxs]

    labels = {'1pdeaat': 'Degrees (C)',
              'pitch': 'Pitch (degrees)',
              'tscpos': 'SIM-Z (steps/1000)',
              }

    scales = {'tscpos': 1000.}

    fmts = {'1pdeaat': '%.2f',
            'pitch': '%.3f',
            'tscpos': '%d'}

    good_mask = np.ones(len(tlm),dtype='bool')
    for interval in model.bad_times:
        bad = ((tlm['date'] >= DateTime(interval[0]).secs)
            & (tlm['date'] < DateTime(interval[1]).secs))
        good_mask[bad] = False

    plots = []
    logger.info('Making PSMC model validation plots and quantile table')
    quantiles = (1, 5, 16, 50, 84, 95, 99)
    # store lines of quantile table in a string and write out later
    quant_table = ''
    quant_head = ",".join(['MSID'] + ["quant%d" % x for x in quantiles])
    quant_table += quant_head + "\n"
    for fig_id, msid in enumerate(sorted(pred)):
        plot = dict(msid=msid.upper())
        fig = plt.figure(10 + fig_id, figsize=(7, 3.5))
        fig.clf()
        scale = scales.get(msid, 1.0)
        ticklocs, fig, ax = plot_cxctime(model.times, tlm[msid] / scale,
                                         fig=fig, fmt='-r')
        ticklocs, fig, ax = plot_cxctime(model.times, pred[msid] / scale,
                                         fig=fig, fmt='-b')
        if  np.any(~good_mask) :
            ticklocs, fig, ax = plot_cxctime(model.times[~good_mask], tlm[msid][~good_mask] / scale,
                                         fig=fig, fmt='.c')

        ax.set_title(msid.upper() + ' validation')
        ax.set_ylabel(labels[msid])
        ax.grid()
        filename = msid + '_valid.png'
        outfile = os.path.join(outdir, filename)
        logger.info('Writing plot file %s' % outfile)
        fig.savefig(outfile)
        plot['lines'] = filename

        # Make quantiles
        if msid == '1pdeaat':
            ok = (( tlm[msid] > 30.0 ) & good_mask )
            ok2 =(( tlm[msid] > 40.0 ) & good_mask )
        else:
            ok = np.ones(len(tlm[msid]), dtype=bool)
        diff = np.sort(tlm[msid][ok] - pred[msid][ok])
        quant_line = "%s" % msid
        for quant in quantiles:
            quant_val = diff[(len(diff) * quant) // 100]
            plot['quant%02d' % quant] = fmts[msid] % quant_val
            quant_line += (',' + fmts[msid] % quant_val)
        quant_table += quant_line + "\n"

        for histscale in ('log', 'lin'):
            fig = plt.figure(20 + fig_id, figsize=(4, 3))
            fig.clf()
            ax = fig.gca()
            ax.hist(diff / scale, bins=50, log=(histscale == 'log'))
            if msid == '1pdeaat':
                diff2=np.sort(tlm[msid][ok2] - pred[msid][ok2])
                ax.hist(diff2 / scale, bins=50, log=(histscale == 'log'),
                        color = 'red')
            ax.set_title(msid.upper() + ' residuals: data - model')
            ax.set_xlabel(labels[msid])
            fig.subplots_adjust(bottom=0.18)
            filename = '%s_valid_hist_%s.png' % (msid, histscale)
            outfile = os.path.join(outdir, filename)
            logger.info('Writing plot file %s' % outfile)
            fig.savefig(outfile)
            plot['hist' + histscale] = filename

        plots.append(plot)
                    
    filename = os.path.join(outdir, 'validation_quant.csv')
    logger.info('Writing quantile table %s' % filename)
    f = open(filename, 'w')
    f.write(quant_table)
    f.close()

    # If run_start is specified this is likely for regression testing
    # or other debugging.  In this case write out the full predicted and
    # telemetered dataset as a pickle.
    if opt.run_start:
        filename = os.path.join(outdir, 'validation_data.pkl')
        logger.info('Writing validation data %s' % filename)
        f = open(filename, 'w')
        pickle.dump({'pred': pred, 'tlm': tlm}, f, protocol=-1)
        f.close()

    # adding stuff for resid plots--rje 6/24/14
    fig = plt.figure(36)
    fig.clf()

    # this is the python equivalent of the IDL where() function
    # note parens are required for the & cases.
    msid='1pdeaat'
    hot_hrcs = ((tlm['tscpos'] < -85000.0 ) & ( pred[msid] > 40.0 ) & good_mask )
    hot_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 ) & ( pred[msid] > 40.0 ) & good_mask )
    hot_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 ) & ( pred[msid] > 40.0 ) & good_mask )
    hot_acisi = ((tlm['tscpos'] > 80000.0 ) & ( pred[msid] > 40.0 ) & good_mask )
    warm_hrcs = ((tlm['tscpos'] < -85000.0 ) & ( pred[msid] > 30.0 ) & ( pred[msid] < 40.0 ) & good_mask )
    warm_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 )& ( pred[msid] > 30.0 ) & ( pred[msid] < 40.0 ) & good_mask )
    warm_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 )& ( pred[msid] > 30.0 ) & ( pred[msid] < 40.0 ) & good_mask )
    warm_acisi = ((tlm['tscpos'] > 80000.0 ) & ( pred[msid] > 30.0 ) & ( pred[msid] < 40.0 ) & good_mask )
    cold_hrcs = ( (tlm['tscpos'] < -85000.0 ) & ( pred[msid] < 30.0 ) & good_mask )
    cold_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 ) & ( pred[msid] < 30.0 ) & good_mask )
    cold_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 ) & ( pred[msid] < 30.0 ) & good_mask )
    cold_acisi = ( (tlm['tscpos'] > 80000.0 ) & ( pred[msid] < 30.0 ) & good_mask )

    plt.plot(tlm['pitch'][hot_hrci],  tlm[msid][hot_hrci] - pred[msid][hot_hrci],  "ob", markersize=5)
    plt.plot(tlm['pitch'][hot_hrcs], tlm[msid][hot_hrcs] - pred[msid][hot_hrcs],  "ok", markersize=5)
    plt.plot(tlm['pitch'][hot_aciss], tlm[msid][hot_aciss] - pred[msid][hot_aciss], "or", markersize=5)
    plt.plot(tlm['pitch'][hot_acisi], tlm[msid][hot_acisi] - pred[msid][hot_acisi], "og", markersize=5)

    plt.plot(tlm['pitch'][warm_hrci], tlm[msid][warm_hrci] - pred[msid][warm_hrci],  "sb", markersize=3)
    plt.plot(tlm['pitch'][warm_hrcs], tlm[msid][warm_hrcs] - pred[msid][warm_hrcs],  "sk", markersize=3)
    plt.plot(tlm['pitch'][warm_aciss], tlm[msid][warm_aciss] - pred[msid][warm_aciss], "sr", markersize=3)
    plt.plot(tlm['pitch'][warm_acisi], tlm[msid][warm_acisi] - pred[msid][warm_acisi], "sg", markersize=3)

    plt.plot(tlm['pitch'][cold_hrci], tlm[msid][cold_hrci] - pred[msid][cold_hrci],  ".b", markersize=2)
    plt.plot(tlm['pitch'][cold_hrcs], tlm[msid][cold_hrcs] - pred[msid][cold_hrcs],  ".k", markersize=2)
    plt.plot(tlm['pitch'][cold_aciss], tlm[msid][cold_aciss] - pred[msid][cold_aciss], ".r", markersize=2)
    plt.plot(tlm['pitch'][cold_acisi], tlm[msid][cold_acisi] - pred[msid][cold_acisi], ".g", markersize=2)
    # plt.plot(tlm['pitch'][htr_on], tlm[msid][htr_on] - pred[msid][htr_on], "*m", markersize=10)

    plt.ylabel('1PDEAAT Data - Model')
    plt.xlabel('pitch angle')
    plt.title('b,k,r,g=hrci,hrcs,aciss,acisi, mod.temp: 0<.<30<s<40<o')
    plt.grid()

    outfile=os.path.join(outdir,'1pdeaat_resid_pitch.png')
    fig.savefig(outfile)

    # adding stuff for resid plots--rje 6/24/14


    fig = plt.figure(35)
    fig.clf()

    # this is the python equivalent of the IDL where() function
    # note parens are required for the & cases.
    fwd_hrcs = ((tlm['tscpos'] < -85000.0 ) & ( tlm['pitch'] < 65.0 ) & good_mask )
    fwd_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 ) & ( tlm['pitch'] < 65.0 ) & good_mask )
    fwd_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 ) & ( tlm['pitch'] < 65.0 ) & good_mask )
    fwd_acisi = ((tlm['tscpos'] > 80000.0 ) & ( tlm['pitch'] < 65.0 ) & good_mask )

    m80_hrcs = ((tlm['tscpos'] < -85000.0 ) & ( tlm['pitch'] > 65.0 ) & ( tlm['pitch'] < 80.0 ) & good_mask )
    m80_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 )& ( tlm['pitch'] > 65.0 ) & ( tlm['pitch'] < 80.0 ) & good_mask )
    m80_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 )& ( tlm['pitch'] > 65.0 ) & ( tlm['pitch'] < 80.0 ) & good_mask )
    m80_acisi = ((tlm['tscpos'] > 80000.0 ) & ( tlm['pitch'] > 65.0 ) & ( tlm['pitch'] < 80.0 ) & good_mask )

    mid_hrcs = ((tlm['tscpos'] < -85000.0 ) & ( tlm['pitch'] > 80.0 ) & ( tlm['pitch'] < 90.0 ) & good_mask )
    mid_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 )& ( tlm['pitch'] > 80.0 ) & ( tlm['pitch'] < 90.0 ) & good_mask )
    mid_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 )& ( tlm['pitch'] > 80.0 ) & ( tlm['pitch'] < 90.0 ) & good_mask )
    mid_acisi = ((tlm['tscpos'] > 80000.0 ) & ( tlm['pitch'] > 80.0 ) & ( tlm['pitch'] < 90.0 ) & good_mask )

    aft_hrcs = ( (tlm['tscpos'] < -85000.0 ) & ( tlm['pitch'] > 90.0 ) & good_mask )
    aft_hrci = ( ( -85000.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 0.0 ) & ( tlm['pitch'] > 90.0 ) & good_mask )
    aft_aciss = ( ( 0.0 < tlm['tscpos'] ) & ( tlm['tscpos'] < 80000.0 ) & ( tlm['pitch'] > 90.0 ) & good_mask )
    aft_acisi = ( (tlm['tscpos'] > 80000.0 ) & ( tlm['pitch'] > 90.0 ) & good_mask )

    msid='1pdeaat'
    plt.plot(pred[msid][fwd_hrci], tlm[msid][fwd_hrci] - pred[msid][fwd_hrci],  "ob", markersize=5)
    plt.plot(pred[msid][fwd_hrcs], tlm[msid][fwd_hrcs] - pred[msid][fwd_hrcs],  "ok", markersize=5)
    plt.plot(pred[msid][fwd_aciss], tlm[msid][fwd_aciss] - pred[msid][fwd_aciss], "or", markersize=5)
    plt.plot(pred[msid][fwd_acisi], tlm[msid][fwd_acisi] - pred[msid][fwd_acisi], "og", markersize=5)

    plt.plot(pred[msid][m80_hrci], tlm[msid][m80_hrci] - pred[msid][m80_hrci],  "vb", markersize=5)
    plt.plot(pred[msid][m80_hrcs], tlm[msid][m80_hrcs] - pred[msid][m80_hrcs],  "vk", markersize=5)
    plt.plot(pred[msid][m80_aciss], tlm[msid][m80_aciss] - pred[msid][m80_aciss], "vr", markersize=5)
    plt.plot(pred[msid][m80_acisi], tlm[msid][m80_acisi] - pred[msid][m80_acisi], "vg", markersize=5)

    plt.plot(pred[msid][mid_hrci], tlm[msid][mid_hrci] - pred[msid][mid_hrci],  "^b", markersize=5)
    plt.plot(pred[msid][mid_hrcs], tlm[msid][mid_hrcs] - pred[msid][mid_hrcs],  "^k", markersize=5)
    plt.plot(pred[msid][mid_aciss], tlm[msid][mid_aciss] - pred[msid][mid_aciss], "^r", markersize=5)
    plt.plot(pred[msid][mid_acisi], tlm[msid][mid_acisi] - pred[msid][mid_acisi], "^g", markersize=5)

    plt.plot(pred[msid][aft_hrci], tlm[msid][aft_hrci] - pred[msid][aft_hrci],  ".b", markersize=2)
    plt.plot(pred[msid][aft_hrcs], tlm[msid][aft_hrcs] - pred[msid][aft_hrcs],  ".k", markersize=2)
    plt.plot(pred[msid][aft_aciss], tlm[msid][aft_aciss] - pred[msid][aft_aciss], ".r", markersize=2)
    plt.plot(pred[msid][aft_acisi], tlm[msid][aft_acisi] - pred[msid][aft_acisi], ".g", markersize=2)

    maxmodeltemp=ndarray.max(pred[msid][good_mask])
    maxresid=ndarray.max(tlm[msid][good_mask]-pred[msid][good_mask])
    x = np.array(np.linspace(52.5-maxresid,maxmodeltemp,num=5))
    my_y = 52.5 - x
    plt.plot( x, my_y )

    plt.ylabel('Data - Model')
    plt.xlabel('1pdeaat Model')
    plt.title('blue,black,red,green=hrci,hrcs,aciss,acisi, 45<o<65<v<80<^<90<.')
    plt.grid()

    # raise ValueError
    outfile=os.path.join(outdir,'1pdeaat_resid.png')
    fig.savefig(outfile)

    return plots

if __name__ == '__main__':
    opt, args = get_options()
    if opt.version:
        print VERSION
        sys.exit(0)

    try:
        data=main(opt)
    except Exception, msg:
        if opt.traceback:
            raise
        else:
            print "ERROR:", msg
            sys.exit(1)
