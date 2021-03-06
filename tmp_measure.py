# -*- coding: utf-8 -*-
"""
Created on Wed Sep 24 12:34:53 2014

@author: brian
"""
import numpy
from numpy import matrix, transpose, zeros
import os, csv
from collections import defaultdict
from multiprocessing import Pool
from functools import partial

from data_preprocessing import preprocess_data, remove_bad_dimensions_grouped
from mahalanobis import *
from traffic_estimation.plot_estimates import make_video, build_speed_dicts
from lof import *
from tools import *

from measureLinkOutliers import load_pace_data, load_from_file
from sys import stdout

NUM_PROCESSORS = 8


#Reads time-series pace data from a file, and sorts it into a convenient format.
#Arguments:
    #dirName - the directory which contains time-series features (produced by extractGridFeatures.py)
#Returns:  (pace_timeseries, var_timeseries, count_timeseries, pace_grouped).  Breakdown:
    #pace_timeseries - a dictionary which maps (date, hour, weekday) to the corresponding average pace vector (average pace of each trip type)
    #var_timeseries - a dictionary which maps (date, hour, weekday) to the corresponding pace variance vector (variance of paces of each trip type)
    #count_timeseries - a dictionary which maps (date, hour, weekday) to the corresponding count vector (number of occurrences of each trip type)
    #pace_grouped - a dictionary which maps (weekday, hour) to the list of corresponding pace vectors
    #        for example, ("Wednesday", 5) maps to the list of all pace vectors that occured on a Wednesday at 5am.
    #trip_names - the names of the trips, which correspond to the dimensions in the vectors (e.g. "E-E")
def readPaceData(dirName):
    logMsg("Reading files from " + dirName + " ...")
    #Create filenames
    paceFileName = os.path.join(dirName, "pace_features.csv")

    
    #Initialize dictionaries    
    pace_timeseries = {}
    pace_grouped = defaultdict(list)
    dates_grouped = defaultdict(list)
    
    #Read the pace file
    r = csv.reader(open(paceFileName, "r"))
    header = r.next()
    colIds = getHeaderIds(header)
    
    #Read the file line by line
    for line in r:
        #Extract info
        #First 3 columns
        date = line[colIds["Date"]]
        hour = int(line[colIds["Hour"]])
        weekday = line[colIds["Weekday"]]
        
        #The rest of the columns contain paces
        paces = map(float, line[3:])
        
        #Convert to numpy column vector
        v = transpose(matrix(paces))
        #Save vector in the timeseries
        pace_timeseries[(date, hour, weekday)] = v
        
        #save the vector into the group
        pace_grouped[(weekday, hour)].append(v)
        dates_grouped[(weekday, hour)].append(date)

    trip_names = header[3:]
    
    #return time series and grouped data
    return (pace_timeseries, pace_grouped, dates_grouped, trip_names)





#Reads the time-series global pace from a file and sorts it into a convenient format
#Arguments:
    #dirName - the directory which contains time-series features (produced by extractGridFeatures.py)
#Returns: - a dictionary which maps (date, hour, weekday) to the average pace of all taxis in that timeslice
def readGlobalPace(dirName):
    paceFileName = os.path.join(dirName, "global_features.csv")
    
    #Read the pace file
    r = csv.reader(open(paceFileName, "r"))
    colIds = getHeaderIds(r.next())
    
    pace_timeseries = {}

        
    
    for line in r:
        #Extract info
        #First 3 columns
        date = line[colIds["Date"]]
        hour = int(line[colIds["Hour"]])
        weekday = line[colIds["Weekday"]]
        #Last 16 columns
        pace = float(line[colIds["Pace"]])
        

        #Save vector in the timeseries and the group
        pace_timeseries[(date, hour, weekday)] = pace

    return pace_timeseries
    
    
    
#Given a pace timeseries, compute the expected value for each timeslice (based on the weekly periodic pattern)
#This is a leave-one-out estimate (e.g. The expected pace for Friday, January 1st at 8am is the average of all Fridays at 8am EXCEPT for Friday January 1st)
#Arguments:
	#global_pace_timeseries - see likelihood_test_parallel.readGlobalPace()
#Returns:
	#A tuple (expected_pace_timeseries, sd_pace_timeseries).  Breakdown:
		#expected_pace_timeseries - A dictionary keyed by (date, hour, weekday) which contains expected paces for each hour of the timeseries
		#expected_pace_timeseries - A dictionary keyed by (date, hour, weekday) which contains the standard deviation of paces at that hour of the time series
def getExpectedPace(global_pace_timeseries):
	#First computed grouped counts, sums, and sums of squares
	#Note that these are leave-one-IN estimates.  This will be converted to leave-one-out in the next step
	grouped_sum = defaultdict(float)
	grouped_ss = defaultdict(float)	
	grouped_count = defaultdict(float)
	#Iterate through all days, updating the corresponding sums
	for (date, hour, weekday) in global_pace_timeseries:
		grouped_sum[weekday, hour] += global_pace_timeseries[date,hour,weekday]
		grouped_ss[weekday, hour] += global_pace_timeseries[date,hour,weekday] ** 2
		
		grouped_count[weekday, hour] += 1
	
	expected_pace_timeseries = {}
	sd_pace_timeseries = {}
	#Now that the grouped stats are computed, iterate through the timeseries again
	for (date, hour, weekday) in global_pace_timeseries:
		#The updated count, sum, and sum of squares are computed by subtracting the observation at hand
		#i.e. a leave-one-out estimate
		updated_sum = grouped_sum[weekday, hour] - global_pace_timeseries[date, hour, weekday]
		updated_ss = grouped_ss[weekday, hour] - global_pace_timeseries[date, hour, weekday] ** 2
		updated_count = grouped_count[weekday, hour] - 1
		
		#Compute the average and standard deviation from these sums
		expected_pace_timeseries[date, hour, weekday] = updated_sum / updated_count
		sd_pace_timeseries[date, hour, weekday] = sqrt((updated_ss / updated_count) - expected_pace_timeseries[date, hour, weekday] ** 2)
	
	#Return the computed time series dictionaries
	return (expected_pace_timeseries, sd_pace_timeseries)    
    
    
    
def reduceOutlierScores(scores, sorted_keys, dates_grouped):
    #weekday_strs = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    #mahals - list of lists
    # dates_grouped - dict of lists
    
    all_entries = []
    for i in xrange(len(sorted_keys)):
        this_hour, this_weekday = sorted_keys[i]
        mahals, c_vals = scores[i]
        for j in xrange(len(mahals)):
            this_date = dates_grouped[sorted_keys[i]][j]
            entry = (this_date, this_hour, this_weekday, mahals[j], c_vals[j])
            all_entries.append(entry)
    
    all_entries.sort()
    return all_entries

    

def generateTimeSeriesOutlierScores(inDir, use_link_db=False, robust=False, num_pcs=10,
                                    gamma=.5, perc_missing_allowed=.05, make_zscore_vid=False,
                                    pool = DefaultPool()):
                                 


    numpy.set_printoptions(linewidth=1000, precision=4)
    
    #Read the time-series data from the file
    logMsg("Reading files...")
    stdout.flush()
    if(use_link_db):
        file_prefix = "link_"
        
        #pace_timeseries, pace_grouped, weights_grouped, dates_grouped, trip_names, consistent_link_set = load_pace_data(
        #    num_trips_threshold=consistent_threshold, pool=pool)
        
        pace_timeseries, pace_grouped, weights_grouped, dates_grouped, trip_names, consistent_link_set = load_from_file('tmp_vectors.pickle')


    else:
        file_prefix = "coarse_"
        (pace_timeseries, pace_grouped, dates_grouped, trip_names) = readPaceData(inDir)


    if(robust):
        robustStr = "RPCA%d" % int(gamma*100)
    else:
        robustStr = "PCA"

    file_prefix += "%s_%s_%dpcs_%dpercmiss" % (inDir, robustStr, num_pcs, perc_missing_allowed*100)

    #pace_grouped = preprocess_data(pace_grouped, num_pcs,
    #                               perc_missing_allowed=perc_missing_allowed)
    pace_grouped = remove_bad_dimensions_grouped(pace_grouped, perc_missing_allowed)



    #Also get global pace information
    global_pace_timeseries = readGlobalPace(inDir)
    (expected_pace_timeseries, sd_pace_timeseries) = getExpectedPace(global_pace_timeseries)

    logMsg("Starting processes")
    logMsg("Doing RPCA with gamma=%f, k=%d" % (gamma, num_pcs))
    stdout.flush()

    # Freeze the parameters of the computeMahalanobisDistances() function
    mahalFunc = partial(computeMahalanobisDistances, robust=robust, k=num_pcs,
                        gamma=gamma)
    
    # Compute all mahalanobis distances
    sorted_keys = sorted(pace_grouped)    
    groups = [pace_grouped[key] for key in sorted_keys]    
    outlier_scores = pool.map(mahalFunc, groups) #Run all of the groups, using as much parallel computing as possible

    logMsg("Merging output")
    #Merge outputs from all of the threads
    entries = reduceOutlierScores(outlier_scores, sorted_keys, dates_grouped)

    
    logMsg("Writing file")
    #Output outlier scores to file
    scoreWriter = csv.writer(open("results/%s_robust_outlier_scores.csv"%file_prefix, "w"))
    scoreWriter.writerow(['date','hour','weekday', 'mahal' ,'c_val','global_pace', 'expected_pace', 'sd_pace'])
    

    for (date, hour, weekday, mahal, c_val) in sorted(entries):
        try:
            gl_pace = global_pace_timeseries[(date, hour, weekday)]
            exp_pace = expected_pace_timeseries[(date, hour, weekday)]
            sd_pace = sd_pace_timeseries[(date, hour, weekday)]
        except:
            gl_pace = 0
            exp_pace = 0
            sd_pace = 0
        
        scoreWriter.writerow([date, hour, weekday, mahal, c_val, gl_pace, exp_pace, sd_pace])


    """
    zscoreWriter= csv.writer(open("results/%szscore.csv"%file_prefix, "w"))
    zscoreWriter.writerow(['Date','Hour','Weekday'] + trip_names)
    #Output zscores to file
    for (date, hour, weekday) in sorted(zscores):
        std_vect = zscores[date, hour, weekday]
        zscoreWriter.writerow([date, hour, weekday] + ravel(std_vect).tolist())
    """
    

    #def make_video(tmp_folder, filename_base, pool=DefaultPool(), dates=None, speed_dicts=None)
    if(make_zscore_vid):
        logMsg("Making speed dicts")
        #zscore_list = [zscores[key] for key in sorted(zscores)]
        date_list = dates = [datetime(2012,10,21) + timedelta(hours=1)*x for x in range(168*3)]
        weekday_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']        
        zscore_list = [zscores[str(d.date()), d.hour, weekday_names[d.weekday()]] for d in date_list]

        speed_dicts = build_speed_dicts(consistent_link_set, zscore_list)
        logMsg("Making video")
        make_video("tmp_vid", "zscore_vid", pool=pool, dates=date_list, speed_dicts=speed_dicts)
        
        
        

    logMsg("Done.")
    
    #pool.close()

if(__name__=="__main__"):
    #logMsg("Running raw analysis")
    #generateTimeSeriesLeave1("4year_features", use_link_db=True)
    
    
    #generateTimeSeriesLeave1("features_imb20_k10", use_link_db=False, num_pcs=3, perc_missing_allowed=.05)
    pool = Pool(8)
    

    for gamma in [.2,.3,.4,.5,.6,.7,.8,.9,1]:
        generateTimeSeriesOutlierScores("features_imb20_k10", use_link_db=True, robust=True, num_pcs=10,
                                        gamma=gamma, perc_missing_allowed=.05, make_zscore_vid=False, pool=pool)
    
    """
    logMsg("Running normalized analysis")
    generateTimeSeriesLeave1("4year_features", use_link_db=True, normalize=True)
    
    logMsg("Running weighted analysis")
    generateTimeSeriesLeave1("4year_features", use_link_db=True, use_feature_weights=True, normalize=True)

    
    logMsg("Running normalized analysis")
    generateTimeSeriesLeave1("4year_features", use_link_db=True, normalize=True, consistent_threshold=20)
    
    logMsg("Running weighted analysis")
    generateTimeSeriesLeave1("4year_features", use_link_db=True, use_feature_weights=True, normalize=True, consistent_threshold=20)

    logMsg("Running normalized analysis")
    generateTimeSeriesLeave1("4year_features", use_link_db=True, normalize=True, consistent_threshold=300)
    
    logMsg("Running weighted analysis")
    generateTimeSeriesLeave1("4year_features", use_link_db=True, use_feature_weights=True, normalize=True, consistent_threshold=300)
    """

